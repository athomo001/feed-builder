"""Adaptador de feed materializado STIX 2.1.

Un `bundle.json` por destino (no uno por subtipo como TXT/CSV -- un bundle
STIX mezcla todos los tipos de indicator en una sola coleccion). Envuelve
`hub/stix_bundle.StixBundleWriter`, igual que `TxtFeedAdapter` envuelve
`FeedWriter`.

A diferencia del servidor TAXII (`hub/adapters/taxii_adapter.py`), este
archivo es una foto del estado actual (se reescribe completo en cada
rebuild) -- descartar/revocar remueve el objeto del bundle, igual que
TXT/CSV/rsc/cdb. TAXII si es append/update-only porque un cliente TAXII
pagina por `added_after` esperando que la coleccion solo crezca.

Autor: Athan Espinoza
"""
import os
from datetime import datetime
from typing import Optional

from hub.adapters.base import AdapterSendResult
from hub.destinations_store import Destination
from hub.models import CanonicalIOCEvent
from hub.stix_bundle import StixBundleWriter, render_stix_indicator


class StixBundleAdapter:
    def __init__(self, destination: Destination, *, base_dir: str):
        self.destination = destination
        self.base_dir = os.path.join(base_dir, destination.destination_id)
        self.writer = StixBundleWriter(
            os.path.join(self.base_dir, "bundle.json"),
            max_records=destination.capacity.get("max_records_per_file", 0),
            max_bytes=destination.capacity.get("max_file_size_bytes", 0),
            overflow_strategy=destination.capacity.get("overflow_strategy", "newest_first"),
        )

    def validate(self) -> list[str]:
        errors = []
        if self.destination.format not in ("stix2.1",):
            errors.append("adapter 'stix_bundle_feed' solo soporta destination.format == 'stix2.1'")
        return errors

    def render(self, event: CanonicalIOCEvent) -> dict:
        return render_stix_indicator(event)

    def send(self, rendered: dict, *, idempotency_key: Optional[str] = None) -> AdapterSendResult:
        # sort_key viene de "modified" (no de un timestamp separado) porque
        # el objeto STIX ya trae su propio campo temporal canonico -- evita
        # mantener dos nociones distintas de "cuando cambio este indicator".
        value = rendered["x_hub_normalized_value"]
        sort_key = datetime.fromisoformat(rendered["modified"].replace("Z", "+00:00")).timestamp()
        self.writer.upsert(value, sort_key=sort_key, stix_object=rendered)
        result = self.writer.rebuild()
        return AdapterSendResult(success=True, detail=f"written={result.written}")

    def discard(self, event: CanonicalIOCEvent) -> AdapterSendResult:
        self.writer.remove(event.normalized_value)
        self.writer.rebuild()
        return AdapterSendResult(success=True, detail="removed")

    def acknowledge(self, result: AdapterSendResult) -> None:
        return None

    def healthcheck(self) -> bool:
        # Prueba de escritura real, no solo un chequeo de ruta: detecta
        # permisos/disco read-only antes de que un send() real falle.
        try:
            os.makedirs(self.base_dir, exist_ok=True)
            probe = os.path.join(self.base_dir, ".healthcheck")
            with open(probe, "w", encoding="utf-8") as f:
                f.write("ok")
            os.remove(probe)
            return True
        except OSError:
            return False

    def close(self) -> None:
        return None
