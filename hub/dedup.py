"""Capas de deduplicacion (spec/04-IOC-MODEL-POLICIES.md "Duplicacion", Entrega 1).

Funciones puras: derivan claves y clasifican un CanonicalIOCEvent contra
conjuntos/diccionarios de "vistos" que el llamador mantiene. El
almacenamiento durable de esos "vistos" (SQLite u otro) es responsabilidad
del Event Ledger (spec/03-ARCHITECTURE.md), no de este modulo.
"""
import hashlib
import json
from datetime import datetime
from typing import Optional

from hub.models import CanonicalIOCEvent, Family
from hub.policy import ReasonCode


def content_version_key(stix_id: str, modified_at: datetime) -> str:
    return f"{stix_id}@{modified_at.isoformat()}"


def content_key(family: Family, subtype: str, normalized_value: str) -> str:
    # spec/04: mantener algoritmos de hash (y en general subtipos) separados.
    return f"{family.value}/{subtype}/{normalized_value}"


def payload_hash(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def classify_duplicate(
    event: CanonicalIOCEvent,
    *,
    seen_event_ids: set,
    seen_content_versions: set,
    seen_content_values: dict,
) -> Optional[ReasonCode]:
    """Devuelve la razon de duplicado que aplica, o None si es un evento
    genuinamente nuevo (debe seguir a evaluacion de politica)."""
    if event.event_id in seen_event_ids:
        return ReasonCode.DUPLICATE_EVENT

    version_key = content_version_key(event.stix_id, event.modified_at)
    if version_key in seen_content_versions:
        return ReasonCode.DUPLICATE_CONTENT

    c_key = content_key(event.family, event.subtype, event.normalized_value)
    prior_version_key = seen_content_values.get(c_key)
    if prior_version_key is not None and prior_version_key != version_key:
        # Mismo valor, version distinta: no se oculta (spec/04), solo se anota.
        return ReasonCode.SAME_VALUE_NEW_VERSION

    return None
