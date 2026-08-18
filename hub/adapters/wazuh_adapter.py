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
import re
from typing import Optional

from hub.adapters.base import AdapterSendResult
from hub.destinations_store import Destination
from hub.models import CanonicalIOCEvent
from hub.ttl import effective_expiration_for_policy
from hub.txt_feed import FeedWriterRegistry

# Prefijo en espanol por subtipo para el valor de la CDB list cuando
# include_tag=true (pedido explicito del operador, 2026-08-18: "clave es el
# IOC, valor es mas o menos lo que es el IOC", ej. "IP_MALICIOSA_BOTNET").
# Subtipos sin entrada explicita caen al fallback generico en _tag_prefix
# (el propio subtipo en mayusculas) -- no hace falta que esta lista este
# completa para que el feature funcione.
_SUBTYPE_PREFIX = {
    "ipv4": "IP", "ipv6": "IP", "cidr": "IP", "mac-address": "MAC", "asn": "ASN",
    "domain": "DOMINIO", "hostname": "DOMINIO", "fqdn": "DOMINIO",
    "url": "URL", "uri": "URL", "user-agent": "USER_AGENT",
    "email": "EMAIL", "username": "USUARIO", "phone": "TELEFONO",
    "md5": "HASH", "sha1": "HASH", "sha224": "HASH", "sha256": "HASH", "sha384": "HASH", "sha512": "HASH",
    "sha3-256": "HASH", "sha3-512": "HASH", "ssdeep": "HASH", "tlsh": "HASH", "imphash": "HASH",
    "authentihash": "HASH", "pehash": "HASH", "custom-hash": "HASH",
    "keyword": "PALABRA_CLAVE", "file-name": "ARCHIVO", "mutex": "MUTEX", "registry-key": "REGISTRO",
    "process-name": "PROCESO", "service-name": "SERVICIO",
    "cve": "CVE", "cwe": "CWE",
}

_NON_CDB_SAFE = re.compile(r"[^A-Z0-9]+")


def _sanitize_for_cdb(text: str) -> str:
    # El valor de una CDB list no puede llevar ':' (es el separador clave:valor
    # del propio formato) -- se normaliza a mayusculas + "_" para cualquier
    # caracter que no sea alfanumerico (labels de OpenCTI pueden traer
    # espacios, guiones, tildes, mayuscula/minuscula mezclada) en vez de
    # sanitizar solo ese caso puntual.
    return _NON_CDB_SAFE.sub("_", text.upper()).strip("_")


def _build_tag(event: CanonicalIOCEvent) -> str:
    prefix = _SUBTYPE_PREFIX.get(event.subtype, event.subtype.upper())
    # Los labels de OpenCTI (objectLabel, sin los que son marcado TLP -- ver
    # hub/normalize.py::_split_labels_and_markings) son lo mas cercano a una
    # clasificacion real del IOC (ej. "botnet", "phishing", "trojan"): se
    # usa el PRIMERO como detalle despues del prefijo -- pedido explicito
    # del operador (2026-08-18): "algo corto". Un indicador real puede traer
    # una docena de labels a la vez (campanias, malware, TTPs...);
    # concatenarlos todos producia valores de 140+ caracteres, lejos de los
    # ejemplos pedidos (IP_MALICIOSA_BOTNET). `hub.normalize` solo saca el
    # marcado TLP de `labels` -- otros marcados con la misma convencion
    # "ALGO:valor" (ej. "PAP:GREEN", visto en datos reales) tambien se
    # excluyen aca: no son clasificacion de amenaza, son control de manejo.
    label = next(
        (raw for raw in event.labels if ":" not in raw),
        None,
    )
    suffix = _sanitize_for_cdb(label) if label else ""
    return f"{prefix}_{suffix}" if suffix else prefix


class WazuhCdbAdapter:
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
        opts = destination.format_options or {}
        # Convencion real de Wazuh para listas de pertenencia booleana: valor
        # vacio despues de los dos puntos. `include_tag=true` en
        # format_options usa el subtype como valor en vez de dejarlo vacio,
        # para operadores que quieran distinguir el tipo de IOC en la lista.
        # "Boolean membership" (valor vacio) sigue siendo el default -- el
        # tag descriptivo (ej. "IP_MALICIOSA_BOTNET") es opt-in, para no
        # sorprender a un operador que ya tenia una lista `include_tag=false`
        # funcionando en su manager.
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
            max_bytes=destination.capacity.get("max_file_size_bytes", 0),
            overflow_strategy=destination.capacity.get("overflow_strategy", "newest_first"),
            subtype_max_records=subtype_max_records,
            extension="cdb",
            render_line=self._render_line,
            parse_line=self._parse_line,
            header=header,
        )

    def _render_line(self, value: str, _sort_key: float, meta: dict) -> str:
        tag = meta.get("tag", "") if self.include_tag else ""
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
        expiration = effective_expiration_for_policy(event, self.ttl_days)
        return {
            "subtype": event.subtype,
            "value": event.normalized_value,
            "sort_key": event.modified_at.timestamp(),
            "meta": {
                # Se calcula siempre (no solo si include_tag), es barato y
                # asi un operador puede prender include_tag despues sin
                # esperar a que cada IOC se vuelva a tocar.
                "tag": _build_tag(event),
                # Vence solo, con el tiempo, via FeedWriter.rebuild() -- ver hub/ttl.py.
                "_expires_at": expiration.isoformat() if expiration else None,
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
