"""Adaptador TXT compatible: envuelve `hub/txt_feed.FeedWriterRegistry` en el
contrato de `hub/adapters/base.py`, con una carpeta propia por destino para
que dos destinos `txt_feed` distintos (por ejemplo Fortinet y pfSense) no
compartan archivos.

Cubre los fabricantes con formato TXT plano ya soportado (Fortinet, Palo
Alto EDL, Cisco Security Intelligence, pfSense/pfBlockerNG -- todos "un IOC
por linea"). Check Point necesita CSV multi-columna real, no una linea por
valor: eso es `hub/adapters/csv_feed_adapter.py`, no este adapter.

Autor: Athan Espinoza
"""
import os
from typing import Optional

from hub.adapters.base import AdapterSendResult
from hub.destinations_store import Destination
from hub.models import CanonicalIOCEvent
from hub.ttl import effective_expiration_for_policy
from hub.txt_feed import FeedWriterRegistry


class TxtFeedAdapter:
    # Un writer por subtype dentro de la carpeta del destino: asi un mismo
    # destino puede recibir IOC de varias familias (ip/domain/hash) sin que
    # terminen mezclados en el mismo archivo de salida.
    def __init__(
        self,
        destination: Destination,
        *,
        base_dir: str,
        subtype_max_records: Optional[dict] = None,
        ttl_days: Optional[dict] = None,
    ):
        self.destination = destination
        self.base_dir = os.path.join(base_dir, destination.destination_id)
        self.ttl_days = ttl_days or {}
        self.registry = FeedWriterRegistry(
            self.base_dir,
            max_records=destination.capacity.get("max_records_per_file", 0),
            max_bytes=destination.capacity.get("max_file_size_bytes", 0),
            overflow_strategy=destination.capacity.get("overflow_strategy", "newest_first"),
            subtype_max_records=subtype_max_records,
        )

    def validate(self) -> list[str]:
        errors = []
        if self.destination.format not in ("txt",):
            errors.append("adapter 'txt_feed' solo soporta destination.format == 'txt'")
        return errors

    def render(self, event: CanonicalIOCEvent) -> dict:
        expiration = effective_expiration_for_policy(event, self.ttl_days)
        return {
            "subtype": event.subtype,
            "value": event.normalized_value,
            "sort_key": event.modified_at.timestamp(),
            "expires_at": expiration.isoformat() if expiration else None,
        }

    def send(self, rendered: dict, *, idempotency_key: Optional[str] = None) -> AdapterSendResult:
        # rebuild_all() reescribe el archivo completo en vez de appendear:
        # es lo que permite aplicar capacity/overflow_strategy (recortar a
        # max_records_per_file) de forma consistente en cada escritura.
        writer = self.registry.get(rendered["subtype"])
        writer.upsert(rendered["value"], sort_key=rendered["sort_key"], meta={"_expires_at": rendered.get("expires_at")})
        results = self.registry.rebuild_all()
        written = results.get(rendered["subtype"])
        return AdapterSendResult(success=True, detail=f"written={written.written if written else 0}")

    def discard(self, event: CanonicalIOCEvent) -> AdapterSendResult:
        # Mismo mecanismo de rebuild que send(): remove() solo saca el valor
        # del set en memoria, el rebuild es lo que materializa el archivo sin
        # ese valor.
        writer = self.registry.get(event.subtype)
        writer.remove(event.normalized_value)
        self.registry.rebuild_all()
        return AdapterSendResult(success=True, detail="removed")

    def acknowledge(self, result: AdapterSendResult) -> None:
        return None

    def healthcheck(self) -> bool:
        # Escribe y borra un archivo real en vez de solo chequear que la ruta
        # existe: confirma permisos de escritura efectivos, que es la falla
        # tipica en destinos file_feed (disco montado read-only, permisos).
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
