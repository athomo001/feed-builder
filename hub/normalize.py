"""Normalizador STIX -> CanonicalIOCEvent.

Orden de clasificacion, de mas confiable a menos confiable:
1. El objeto STIX/observable y su campo explicito (main_observable_type).
2. pattern_type y el patron STIX validado (regex sobre hashes.'ALGO' = '...').
3. Un mapeo de adaptador documentado (STIX_OBSERVABLE_TYPE_TO_FAMILY_SUBTYPE).
4. Inferencia por formato solo como ultimo recurso, con classification_confidence
   reducido (nunca 1.0) para que una politica estricta pueda excluirlo.

Cobertura actual: hash (via pattern STIX) y los observable_type de red/web/
identidad mapeados explicitamente abajo. Cualquier otro tipo se rechaza con
UnclassifiedIndicatorError en vez de adivinar: un IOC sin subtipo confiable
queda sin clasificar en vez de propagarse con un subtipo inventado.

Autor: Athan Espinoza
"""
import re
from datetime import datetime, timezone
from typing import Optional

from hub.models import CanonicalIOCEvent, Family, Operation


class UnclassifiedIndicatorError(ValueError):
    pass


ACTION_TO_OPERATION = {
    "create": Operation.CREATE,
    "update": Operation.UPDATE,
    "delete": Operation.DELETE,
}

STIX_HASH_KEY_TO_SUBTYPE = {
    "MD5": "md5",
    "SHA-1": "sha1",
    "SHA-224": "sha224",
    "SHA-256": "sha256",
    "SHA-384": "sha384",
    "SHA-512": "sha512",
    "SHA3-256": "sha3-256",
    "SHA3-512": "sha3-512",
    "SSDEEP": "ssdeep",
    "TLSH": "tlsh",
    "IMPHASH": "imphash",
    "AUTHENTIHASH": "authentihash",
    "PEHASH": "pehash",
}

HASH_LENGTH_TO_SUBTYPE = {32: "md5", 40: "sha1", 56: "sha224", 64: "sha256", 96: "sha384", 128: "sha512"}

STIX_OBSERVABLE_TYPE_TO_FAMILY_SUBTYPE = {
    "IPv4-Addr": (Family.NETWORK, "ipv4"),
    "IPv6-Addr": (Family.NETWORK, "ipv6"),
    "Domain-Name": (Family.WEB, "domain"),
    "Hostname": (Family.WEB, "hostname"),
    "Url": (Family.WEB, "url"),
    "Email-Addr": (Family.IDENTITY, "email"),
}

_HASH_PATTERN_RE = re.compile(r"hashes\.'?([A-Za-z0-9_-]+)'?\s*=\s*'([^']+)'", re.IGNORECASE)


def _first_extension(stix: dict) -> dict:
    # El objeto STIX puede traer varias extensiones registradas bajo IDs
    # distintos; solo interesa la primera con forma de dict (la extension de
    # OpenCTI), asi que se evita acoplar el codigo a un ID de extension fijo.
    exts = stix.get("extensions") or {}
    for value in exts.values():
        if isinstance(value, dict):
            return value
    return {}


def _first_observable_value(ext: dict) -> Optional[str]:
    values = ext.get("observable_values") or []
    if values and isinstance(values[0], dict):
        return values[0].get("value")
    return None


def classify_stix(stix: dict):
    """Devuelve (family, subtype, value, classification_confidence) o levanta
    UnclassifiedIndicatorError."""
    ext = _first_extension(stix)
    main_type = ext.get("main_observable_type")

    # StixFile se trata aparte porque "hash" no es un observable_type propio:
    # el algoritmo concreto (md5, sha256, ...) viene codificado en el patron
    # STIX, no en main_observable_type, asi que necesita su propia logica.
    if main_type == "StixFile":
        m = _HASH_PATTERN_RE.search(stix.get("pattern") or "")
        if m:
            subtype = STIX_HASH_KEY_TO_SUBTYPE.get(m.group(1).upper())
            if subtype:
                return Family.HASH, subtype, m.group(2), 1.0

        value = _first_observable_value(ext)
        if value:
            subtype = HASH_LENGTH_TO_SUBTYPE.get(len(value))
            if subtype:
                # Inferir el algoritmo solo por la longitud del valor es
                # ambiguo (varios algoritmos comparten longitud de digest);
                # por eso es el ultimo recurso y va con confianza reducida
                # en vez de 1.0, para que una politica estricta pueda excluirlo.
                return Family.HASH, subtype, value, 0.6
        raise UnclassifiedIndicatorError("StixFile sin algoritmo de hash reconocible")

    if main_type in STIX_OBSERVABLE_TYPE_TO_FAMILY_SUBTYPE:
        value = _first_observable_value(ext)
        if not value:
            raise UnclassifiedIndicatorError(f"{main_type} sin observable_values")
        family, subtype = STIX_OBSERVABLE_TYPE_TO_FAMILY_SUBTYPE[main_type]
        return family, subtype, value, 1.0

    raise UnclassifiedIndicatorError(f"main_observable_type '{main_type}' no tiene mapeo de adaptador")


def _extract_stix(envelope: dict) -> dict:
    # El envelope llega con formas distintas segun la fuente (Live Stream
    # anida el objeto en data.data; el adaptador de GraphQL, en cambio, puede
    # entregarlo mas plano, o incluso pasar el objeto STIX directo en tests).
    # Se prueban las variantes conocidas en orden en vez de asumir una sola.
    data = envelope.get("data")
    if isinstance(data, dict):
        inner = data.get("data")
        if isinstance(inner, dict):
            return inner
        if isinstance(data.get("type"), str):
            return data
    if isinstance(envelope.get("type"), str):
        return envelope
    raise ValueError("no se pudo ubicar el objeto STIX dentro del envelope")


def _split_labels_and_markings(labels: list[str]):
    # OpenCTI representa los marcados TLP como labels con prefijo "tlp:" en
    # vez de como un campo de marking dedicado en este flujo; se separan aqui
    # para que el resto del sistema trate markings y labels como conceptos
    # distintos, tal como los expone CanonicalIOCEvent.
    markings, remaining_labels = [], []
    for label in labels or []:
        if label.lower().startswith("tlp:"):
            markings.append(label.upper())
        else:
            remaining_labels.append(label)
    return markings, remaining_labels


def _to_datetime(value) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def normalize_stix_indicator(envelope: dict, *, event_id: str, source_id: str) -> CanonicalIOCEvent:
    operation = ACTION_TO_OPERATION.get(envelope.get("action"))
    if operation is None:
        raise ValueError(f"accion de envelope desconocida: {envelope.get('action')!r}")

    stix = _extract_stix(envelope)
    ext = _first_extension(stix)
    family, subtype, value, classification_confidence = classify_stix(stix)

    markings, labels = _split_labels_and_markings(stix.get("labels") or [])

    # score/confidence/detection pueden faltar en objetos STIX de origenes
    # externos que no pasan por las extensiones de OpenCTI; se normaliza a
    # un valor "neutro" (0 / False) en vez de dejar el campo ausente, porque
    # el modelo canonico los declara como no-opcionales.
    score = ext.get("score")
    confidence = stix.get("confidence")
    detection = ext.get("detection", False)

    return CanonicalIOCEvent(
        event_id=event_id,
        stix_id=stix["id"],
        operation=operation,
        family=family,
        subtype=subtype,
        source_value=value,
        normalized_value=value.lower() if family == Family.HASH else value,
        display_value=value,
        classification_confidence=classification_confidence,
        score=score if score is not None else 0,
        confidence=confidence if confidence is not None else 0,
        detection=bool(detection),
        revoked=bool(stix.get("revoked", False)),
        markings=markings,
        labels=labels,
        created_at=_to_datetime(stix.get("created")),
        modified_at=_to_datetime(stix.get("modified")),
        valid_until=_to_datetime(stix.get("valid_until")),
        source_id=source_id,
    )
