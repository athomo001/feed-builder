"""Capas de deduplicacion.

Funciones puras: derivan claves y clasifican un CanonicalIOCEvent contra
conjuntos/diccionarios de "vistos" que el llamador mantiene. El
almacenamiento durable de esos "vistos" (SQLite u otro) es responsabilidad
de quien mantiene el ledger de eventos, no de este modulo -- asi la logica
de clasificacion se puede probar sin tocar una base de datos.

Autor: Athan Espinoza
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
    # El subtipo forma parte de la clave para no mezclar coincidencias entre
    # algoritmos o formatos distintos (ej. un mismo string podria calzar por
    # casualidad como md5 y como otro subtipo sin ser el mismo IOC real).
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
    # Se evalua en orden de especificidad creciente: primero el mismo evento
    # exacto (reentrega), luego la misma version de contenido (reprocesado),
    # y solo al final el caso mas sutil de mismo valor con version distinta.
    if event.event_id in seen_event_ids:
        return ReasonCode.DUPLICATE_EVENT

    version_key = content_version_key(event.stix_id, event.modified_at)
    if version_key in seen_content_versions:
        return ReasonCode.DUPLICATE_CONTENT

    c_key = content_key(event.family, event.subtype, event.normalized_value)
    prior_version_key = seen_content_values.get(c_key)
    if prior_version_key is not None and prior_version_key != version_key:
        # Mismo valor, version distinta: no se oculta el evento, solo se anota
        # la razon, porque el operador puede querer ver igual la actualizacion
        # de metadata aunque el valor normalizado no haya cambiado.
        return ReasonCode.SAME_VALUE_NEW_VERSION

    return None
