"""Modelo canonico de evento IOC (spec/04-IOC-MODEL-POLICIES.md, Entrega 0).

Traduccion de nombres de familia: la tabla de spec/04 usa etiquetas en
espanol para el operador (Hash, Red, Web, Identidad, Contenido,
Vulnerabilidad, Custom); el codigo usa identificadores en ingles
(network, identity, content, vulnerability) para mantener el vocabulario
del codigo consistente en un solo idioma. "hash", "custom" no cambian.
"""
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, model_validator


class Operation(str, Enum):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"


class Family(str, Enum):
    HASH = "hash"
    NETWORK = "network"
    WEB = "web"
    IDENTITY = "identity"
    CONTENT = "content"
    VULNERABILITY = "vulnerability"
    CUSTOM = "custom"


# Catalogo inicial de subtipos por familia (spec/04, tabla "Familias y subtipos
# iniciales"). Es extensible: un nuevo subtipo requiere validador, normalizador,
# pruebas, documentacion y matriz de destinos compatibles (ver spec/04).
FAMILY_SUBTYPES = {
    Family.HASH: {
        "md5", "sha1", "sha224", "sha256", "sha384", "sha512",
        "sha3-256", "sha3-512", "ssdeep", "tlsh", "imphash",
        "authentihash", "pehash", "custom-hash",
    },
    Family.NETWORK: {"ipv4", "ipv6", "cidr", "mac-address", "asn"},
    Family.WEB: {"url", "domain", "hostname", "fqdn", "uri", "user-agent"},
    Family.IDENTITY: {"email", "username", "phone"},
    Family.CONTENT: {
        "keyword", "file-name", "mutex", "registry-key",
        "process-name", "service-name",
    },
    Family.VULNERABILITY: {"cve", "cwe"},
    # Family.CUSTOM no tiene catalogo cerrado: "subtipo registrado por adaptador".
}


class CanonicalIOCEvent(BaseModel):
    event_id: str
    stix_id: str
    operation: Operation
    family: Family
    subtype: str
    source_value: str
    normalized_value: str
    display_value: str
    classification_confidence: float = Field(ge=0.0, le=1.0)
    score: int = Field(ge=0, le=100)
    confidence: int = Field(ge=0, le=100)
    detection: bool = False
    markings: list[str] = Field(default_factory=list)
    labels: list[str] = Field(default_factory=list)
    created_at: datetime
    modified_at: datetime
    valid_until: Optional[datetime] = None
    source_id: str

    @model_validator(mode="after")
    def _subtype_belongs_to_family(self) -> "CanonicalIOCEvent":
        allowed = FAMILY_SUBTYPES.get(self.family)
        if allowed is not None and self.subtype not in allowed:
            raise ValueError(
                f"subtype '{self.subtype}' is not valid for family '{self.family.value}'; "
                f"allowed: {sorted(allowed)}"
            )
        return self
