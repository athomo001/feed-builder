"""Adaptador Wazuh CDB list.

Una CDB list (constant database) es texto plano `clave:valor` referenciada
en `ossec.conf`. Wazuh **no** hace *poll* a una URL: el archivo debe existir
en el filesystem del manager y requiere reload para tomar el cambio. Por
eso el Hub **solo materializa el archivo CDB**, igual que cualquier otro
`file_feed` -- no se construye un agente/sync-companion que se conecte al
manager de Wazuh, porque eso abriria una superficie de credenciales/SSH
nueva que va mas alla de lo que un Hub de distribucion de IOC deberia
hacer. Sincronizar el archivo al manager y disparar el reload queda como
responsabilidad externa al operador/automatizacion de su lado.

Autor: Athan Espinoza
"""
import os
from typing import Optional

from hub.adapters.base import AdapterSendResult
from hub.destinations_store import Destination
from hub.models import CanonicalIOCEvent
from hub.txt_feed import FeedWriterRegistry


class WazuhCdbAdapter:
    def __init__(self, destination: Destination, *, base_dir: str):
        self.destination = destination
        self.base_dir = os.path.join(base_dir, destination.destination_id)
        opts = destination.format_options or {}
        # Convencion real de Wazuh para listas de pertenencia booleana: valor
        # vacio despues de los dos puntos. `include_tag=true` en
        # format_options usa el subtype como valor en vez de dejarlo vacio,
        # para operadores que quieran distinguir el tipo de IOC en la lista.
        self.include_tag: bool = bool(opts.get("include_tag", False))
        # Advertencia dejada dentro del propio archivo, no solo en el
        # docstring del modulo: el operador que abra el .cdb en el manager
        # necesita ver ahi mismo que sincronizar/reload es tarea suya.
        header = (
            "# Generado por el Hub -- CDB list (clave:valor). Sincronizar al "
            "filesystem del manager de Wazuh y disparar el reload es "
            "responsabilidad externa."
        )
        self.registry = FeedWriterRegistry(
            self.base_dir,
            max_records=destination.capacity.get("max_records_per_file", 0),
            overflow_strategy=destination.capacity.get("overflow_strategy", "newest_first"),
            extension="cdb",
            render_line=self._render_line,
            parse_line=self._parse_line,
            header=header,
        )

    def _render_line(self, value: str, _sort_key: float, meta: dict) -> str:
        tag = meta.get("subtype", "") if self.include_tag else ""
        return f"{value}:{tag}"

    def _parse_line(self, line: str) -> Optional[str]:
        # Usado al recargar el archivo .cdb existente en memoria: ignora la
        # linea de header (empieza con '#') y cualquier linea sin el
        # separador ':' que no pueda ser una entrada CDB valida.
        if line.startswith("#") or ":" not in line:
            return None
        return line.split(":", 1)[0]

    def validate(self) -> list[str]:
        errors = []
        if self.destination.format not in ("cdb",):
            errors.append("adapter 'wazuh_cdb' solo soporta destination.format == 'cdb'")
        return errors

    def render(self, event: CanonicalIOCEvent) -> dict:
        return {
            "subtype": event.subtype,
            "value": event.normalized_value,
            "sort_key": event.modified_at.timestamp(),
            "meta": {"subtype": event.subtype},
        }

    def send(self, rendered: dict, *, idempotency_key: Optional[str] = None) -> AdapterSendResult:
        writer = self.registry.get(rendered["subtype"])
        writer.upsert(rendered["value"], sort_key=rendered["sort_key"], meta=rendered["meta"])
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
