"""Adaptador MikroTik RouterOS `.rsc`.

RouterOS no tiene EDL/TAXII nativo. El patron de comunidad es: `/tool
fetch` + Scheduler descarga un script y puebla `address-list` mediante un
script RouterOS (`.rsc`) con comandos `add address=... list=...` -- no un
TXT plano. No hay una plantilla `.rsc` completa estandar (sin convencion de
flags de `/tool fetch` como `check-certificate`/`mode`/`dst-path`, ni de
nombre de lista), asi que este adapter genera solo el bloque `add` por
valor, con un comentario en el propio archivo indicando que el operador debe
envolverlo en su propio Scheduler/`/tool fetch`: es una limitacion
documentada a proposito en vez de inventar una plantilla no verificada
contra un router real.

RouterOS `/ip firewall address-list` solo acepta IPv4/IPv6/CIDR -- nunca
dominios ni hashes -- asi que este adapter rechaza en `validate()` cualquier
`allowed_ioc_types` fuera de la familia `network`.

El `timeout=` de RouterOS (auto-remueve la entrada al vencer) mapea de forma
natural al `valid_until` del IOC: se recalcula en cada rebuild a partir del
tiempo restante real, no se congela en el momento del evento.

Autor: Athan Espinoza
"""
import os
import re
from datetime import datetime, timezone
from typing import Optional

from hub.adapters.base import AdapterSendResult
from hub.destinations_store import Destination
from hub.models import CanonicalIOCEvent
from hub.txt_feed import FeedWriterRegistry

# Unicos tipos de IOC que RouterOS address-list puede almacenar; usado por
# validate() para rechazar destinos configurados con family/subtype que
# nunca podrian escribirse en un .rsc valido.
_SUPPORTED_PREFIXES = ("network/ipv4", "network/ipv6", "network/cidr")
_ADDRESS_RE = re.compile(r"address=(\S+)")


# RouterOS espera el timeout en su propio formato "Xd HH:MM:SS", no segundos
# crudos ni ISO8601.
def _format_routeros_timeout(seconds: float) -> str:
    total = max(0, int(seconds))
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{days}d{hours:02d}:{minutes:02d}:{secs:02d}"


class MikrotikAdapter:
    def __init__(self, destination: Destination, *, base_dir: str):
        self.destination = destination
        self.base_dir = os.path.join(base_dir, destination.destination_id)
        opts = destination.format_options or {}
        self.list_name = opts.get("list_name", destination.destination_id)
        # Advertencia dejada dentro del propio archivo .rsc (no solo en el
        # docstring): el operador que abra el archivo en el router necesita
        # saber, sin leer el codigo del Hub, que falta envolverlo en su
        # Scheduler/`/tool fetch`.
        header = (
            "# Generado por el Hub -- solo el bloque 'add'. Envolver en el "
            "propio Scheduler/`/tool fetch` del router (sin plantilla "
            "completa documentada por el fabricante)."
        )
        self.registry = FeedWriterRegistry(
            self.base_dir,
            max_records=destination.capacity.get("max_records_per_file", 0),
            overflow_strategy=destination.capacity.get("overflow_strategy", "newest_first"),
            extension="rsc",
            render_line=self._render_line,
            parse_line=self._parse_line,
            header=header,
        )

    def _render_line(self, value: str, _sort_key: float, meta: dict) -> str:
        comment = f"{meta.get('subtype', '')}:{meta.get('event_id', '')}"
        parts = [f"add address={value}", f"list={self.list_name}", f'comment="{comment}"']
        valid_until_raw = meta.get("valid_until")
        if valid_until_raw:
            # Recalculado a partir del tiempo restante REAL en cada rebuild
            # (no el remaining del momento del evento original), para que el
            # timeout de RouterOS siga siendo correcto aunque el archivo se
            # reescriba mucho despues de que llego el IOC.
            valid_until = datetime.fromisoformat(valid_until_raw)
            remaining = (valid_until - datetime.now(timezone.utc)).total_seconds()
            if remaining > 0:
                parts.append(f"timeout={_format_routeros_timeout(remaining)}")
        return "/ip firewall address-list " + " ".join(parts)

    def _parse_line(self, line: str) -> Optional[str]:
        match = _ADDRESS_RE.search(line)
        return match.group(1) if match else None

    def validate(self) -> list[str]:
        errors = []
        if self.destination.format not in ("rsc",):
            errors.append("adapter 'mikrotik_rsc' solo soporta destination.format == 'rsc'")
        # Rechaza en configuracion, no en tiempo de envio: mejor que el
        # operador vea el error al crear el destino que descubrir en runtime
        # que RouterOS no puede aceptar un dominio/hash en address-list.
        unsupported = [t for t in self.destination.allowed_ioc_types if not t.startswith(_SUPPORTED_PREFIXES)]
        if unsupported:
            errors.append(
                f"adapter 'mikrotik_rsc' solo soporta IOC de red (ipv4/ipv6/cidr): "
                f"RouterOS address-list no acepta {unsupported}"
            )
        return errors

    def render(self, event: CanonicalIOCEvent) -> dict:
        return {
            "subtype": event.subtype,
            "value": event.normalized_value,
            "sort_key": event.modified_at.timestamp(),
            "meta": {
                "subtype": event.subtype,
                "event_id": event.event_id,
                "valid_until": event.valid_until.isoformat() if event.valid_until else None,
            },
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
        # Prueba de escritura real (crea y borra un archivo) en vez de solo
        # verificar la ruta, para exponer problemas de permisos/disco antes
        # de que ocurran durante un send() real.
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
