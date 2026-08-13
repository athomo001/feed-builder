"""Adaptador TXT compatible (spec/09-ROADMAP-ACCEPTANCE.md Entrega 2:
"Adaptador TXT compatible"). Envuelve `hub/txt_feed.FeedWriterRegistry`
(construido en Entrega 1) en el contrato de `hub/adapters/base.py`, con una
carpeta propia por destino para que dos destinos `txt_feed` distintos
(por ejemplo Fortinet y pfSense) no compartan archivos.

Cubre los fabricantes `file_feed` con formato TXT/CIDR ya soportado
(spec/05 "Modos de entrega y esfuerzo relativo": Fortinet, Palo Alto,
Cisco Security Intelligence, pfSense/pfBlockerNG, Check Point-CSV).
"""
import os
from typing import Optional

from hub.adapters.base import AdapterSendResult
from hub.destinations_store import Destination
from hub.models import CanonicalIOCEvent
from hub.txt_feed import FeedWriterRegistry


class TxtFeedAdapter:
    def __init__(self, destination: Destination, *, base_dir: str):
        self.destination = destination
        self.base_dir = os.path.join(base_dir, destination.destination_id)
        self.registry = FeedWriterRegistry(
            self.base_dir,
            max_records=destination.capacity.get("max_records_per_file", 0),
            overflow_strategy=destination.capacity.get("overflow_strategy", "newest_first"),
        )

    def validate(self) -> list[str]:
        errors = []
        if self.destination.format not in ("txt",):
            errors.append("adapter 'txt_feed' solo soporta destination.format == 'txt'")
        return errors

    def render(self, event: CanonicalIOCEvent) -> dict:
        return {
            "subtype": event.subtype,
            "value": event.normalized_value,
            "sort_key": event.modified_at.timestamp(),
        }

    def send(self, rendered: dict, *, idempotency_key: Optional[str] = None) -> AdapterSendResult:
        writer = self.registry.get(rendered["subtype"])
        writer.upsert(rendered["value"], sort_key=rendered["sort_key"])
        results = self.registry.rebuild_all()
        written = results.get(rendered["subtype"])
        return AdapterSendResult(success=True, detail=f"written={written.written if written else 0}")

    def discard(self, event: CanonicalIOCEvent) -> AdapterSendResult:
        writer = self.registry.get(event.subtype)
        writer.remove(event.normalized_value)
        self.registry.rebuild_all()
        return AdapterSendResult(success=True, detail="removed")

    def acknowledge(self, result: AdapterSendResult) -> None:
        return None

    def healthcheck(self) -> bool:
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
