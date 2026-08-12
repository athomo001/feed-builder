"""Contrato de decisiones de politica (Entrega 0).

spec/03-ARCHITECTURE.md "Policy engine": evalua politicas inmutables por
version y devuelve accepted/rejected/skipped/revoked mas codigo de razon.
spec/04-IOC-MODEL-POLICIES.md "Filtros de seguridad" y "Politica de
duplicados" enumeran las razones concretas.
"""
from enum import Enum

from pydantic import BaseModel


class PolicyOutcome(str, Enum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    SKIPPED = "skipped"
    REVOKED = "revoked"


class ReasonCode(str, Enum):
    OK = "ok"

    # spec/04 "Filtros de seguridad"
    REVOKED = "revoked"
    EXPIRED = "expired"
    TLP_NOT_ALLOWED = "tlp_not_allowed"
    SCORE_BELOW_MINIMUM = "score_below_minimum"
    CONFIDENCE_BELOW_MINIMUM = "confidence_below_minimum"
    DETECTION_REQUIRED = "detection_required"
    UNCLASSIFIED = "unclassified"
    SUBTYPE_NOT_ALLOWED = "subtype_not_allowed"

    # spec/04 "Politica de duplicados"
    DUPLICATE_EVENT = "duplicate_event"
    DUPLICATE_CONTENT = "duplicate_content"
    ALREADY_DELIVERED = "already_delivered"
    SAME_VALUE_NEW_VERSION = "same_value_new_version"

    # spec/04 "Capacidad y throughput por destino"
    SKIPPED_CAPACITY = "skipped_capacity"


class PolicyDecision(BaseModel):
    outcome: PolicyOutcome
    reason: ReasonCode = ReasonCode.OK
    policy_version: int
