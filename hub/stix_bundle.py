"""Renderizado STIX 2.1 nativo (spec/09-ROADMAP-ACCEPTANCE.md Entrega 4
"Integraciones", alto esfuerzo: "STIX 2.1 nativo"; spec/05-FORMATS-
DESTINATIONS.md "STIX 2.1: Bundle preservando id, type, created, modified,
labels, markings, confidence y revoked").

Un `CanonicalIOCEvent` se traduce a un SDO `indicator` de STIX 2.1. El
`pattern` usa el Cyber Observable estandar por familia/subtipo cuando existe
uno (`ipv4-addr`, `domain-name`, `url`, `file:hashes...`); para lo que no
tiene un SCO estandar (telefono, user-agent, IOC de contenido como
mutex/registry-key) se usa un SCO custom `x-hub-<subtype>` (STIX 2.1
permite tipos custom con prefijo `x-`, seccion 7 de la spec STIX) en vez de
forzarlo dentro de un tipo que no le corresponde.

El diccionario `hashes` de STIX es vocabulario abierto (no una enumeracion
cerrada): los algoritmos sin nombre "famoso" (SHA-224, SHA-384, imphash,
authentihash, pehash, custom-hash) se escriben tal cual, en mayusculas, en
vez de convertirlos silenciosamente a SHA-256 u otro algoritmo que no son
(spec/09 "Mantiene algoritmos de hash separados y no mezcla... por
defecto").
"""
import json
import os
import uuid
from typing import Optional

from hub.models import CanonicalIOCEvent, Family
from hub.txt_feed import FeedWriteResult, OverflowStrategy

# Namespace fijo y propio del Hub para generar IDs STIX deterministicos
# (mismo event_id siempre produce el mismo indicator--<uuid>, para que un
# update no cree un objeto STIX duplicado en la coleccion TAXII).
_NAMESPACE = uuid.UUID("d29d7b1e-6d2b-4c1a-9d5e-2b6f7a8c9d0e")

_HASH_STIX_NAMES = {
    "md5": "MD5",
    "sha1": "SHA-1",
    "sha256": "SHA-256",
    "sha512": "SHA-512",
    "sha3-256": "SHA3-256",
    "sha3-512": "SHA3-512",
    "ssdeep": "SSDEEP",
    "tlsh": "TLSH",
}


def _stix_timestamp(dt) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def _pattern_for(event: CanonicalIOCEvent) -> str:
    family, subtype, value = event.family, event.subtype, event.normalized_value
    escaped = value.replace("\\", "\\\\").replace("'", "\\'")

    if family == Family.NETWORK:
        if subtype == "ipv4":
            return f"[ipv4-addr:value = '{escaped}']"
        if subtype == "ipv6":
            return f"[ipv6-addr:value = '{escaped}']"
        if subtype == "cidr":
            sco = "ipv6-addr" if ":" in value else "ipv4-addr"
            return f"[{sco}:value = '{escaped}']"
        if subtype == "mac-address":
            return f"[mac-addr:value = '{escaped}']"
        if subtype == "asn":
            return f"[autonomous-system:number = '{escaped}']"
    if family == Family.WEB:
        if subtype in ("domain", "hostname", "fqdn"):
            return f"[domain-name:value = '{escaped}']"
        if subtype in ("url", "uri"):
            return f"[url:value = '{escaped}']"
    if family == Family.HASH:
        algo = _HASH_STIX_NAMES.get(subtype, subtype.upper())
        return f"[file:hashes.'{algo}' = '{escaped}']"
    if family == Family.IDENTITY:
        if subtype == "email":
            return f"[email-addr:value = '{escaped}']"
        if subtype == "username":
            return f"[user-account:user_id = '{escaped}']"

    # Sin SCO estandar para esta familia/subtipo (identity/phone, content/*,
    # vulnerability/*, web/user-agent): SCO custom, no un tipo que no le
    # corresponde.
    return f"[x-hub-{family.value}-{subtype}:value = '{escaped}']"


def render_stix_indicator(event: CanonicalIOCEvent) -> dict:
    indicator_id = f"indicator--{uuid.uuid5(_NAMESPACE, event.event_id)}"
    obj: dict = {
        "type": "indicator",
        "spec_version": "2.1",
        "id": indicator_id,
        "created": _stix_timestamp(event.created_at),
        "modified": _stix_timestamp(event.modified_at),
        "pattern": _pattern_for(event),
        "pattern_type": "stix",
        "valid_from": _stix_timestamp(event.created_at),
        "labels": event.labels or [event.subtype],
        "confidence": event.confidence,
        "revoked": event.revoked,
        # Extensiones propias del Hub (STIX 2.1 seccion 7 "Custom Properties",
        # prefijo x_): trazabilidad hacia el evento canonico y datos que no
        # tienen un campo STIX estandar equivalente.
        "x_hub_event_id": event.event_id,
        "x_hub_score": event.score,
        # Clave de dedup propia (no un campo estandar de STIX): permite
        # releer el bundle desde disco y reconstruir que IOC corresponde a
        # cada objeto, igual que FeedWriter con TXT/CSV (StixBundleWriter
        # de abajo es de vida corta, mismo razonamiento).
        "x_hub_normalized_value": event.normalized_value,
    }
    if event.valid_until:
        obj["valid_until"] = _stix_timestamp(event.valid_until)
    if event.markings:
        # Sin IDs de marking-definition reales (el Hub no los modela) -- se
        # listan como texto en una extension propia en vez de fingir
        # referencias STIX a objetos que no existen.
        obj["x_hub_markings"] = event.markings
    return obj


def render_stix_bundle(objects: list[dict], *, bundle_id: Optional[str] = None) -> dict:
    return {
        "type": "bundle",
        "id": bundle_id or f"bundle--{uuid.uuid4()}",
        "objects": objects,
    }


class StixBundleWriter:
    """Bloque que `hub/adapters/stix_bundle_adapter.py` envuelve -- mismo rol
    que `FeedWriter` para TXT/CSV/rsc/cdb (`hub/txt_feed.py`), pero un bundle
    STIX es un solo JSON con un array `objects`, no una linea por valor, asi
    que no encaja en el modelo linea-por-valor de `FeedWriter`. Replica el
    mismo patron: atomic write, capacidad/overflow, releido desde disco
    porque el adapter que lo envuelve es de vida corta (se reconstruye por
    evento/request, igual que el resto desde Entrega 2)."""

    def __init__(self, path: str, *, max_records: int = 0, overflow_strategy: OverflowStrategy = "newest_first"):
        self.path = path
        self.max_records = max_records
        self.overflow_strategy = overflow_strategy
        # normalized_value -> (sort_key, stix_object)
        self._objects: dict[str, tuple[float, dict]] = {}
        self._load_existing()

    def _load_existing(self) -> None:
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                bundle = json.load(f)
        except (OSError, json.JSONDecodeError):
            return
        for obj in bundle.get("objects", []):
            value = obj.get("x_hub_normalized_value")
            if value:
                self._objects[value] = (float("-inf"), obj)

    def upsert(self, value: str, *, sort_key: float, stix_object: dict) -> None:
        self._objects[value] = (sort_key, stix_object)

    def remove(self, value: str) -> None:
        self._objects.pop(value, None)

    def __len__(self) -> int:
        return len(self._objects)

    def rebuild(self) -> FeedWriteResult:
        ordered = sorted(self._objects.items(), key=lambda kv: (-kv[1][0], kv[0]))
        skipped_capacity = 0
        if self.max_records and len(ordered) > self.max_records:
            skipped_capacity = len(ordered) - self.max_records
            ordered = ordered[: self.max_records]
        objects = [obj for _value, (_sort_key, obj) in ordered]
        bundle = render_stix_bundle(objects)

        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp_path = self.path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(bundle, f, indent=2)
        os.replace(tmp_path, self.path)

        return FeedWriteResult(written=len(objects), skipped_capacity=skipped_capacity)
