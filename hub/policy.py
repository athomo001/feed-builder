"""Contrato de decisiones de politica: el motor de politicas evalua
politicas inmutables por version y devuelve accepted/rejected/skipped/
revoked mas un codigo de razon concreto.

Autor: Athan Espinoza
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

    # Razones de rechazo por filtros de seguridad basicos del IOC.
    REVOKED = "revoked"
    EXPIRED = "expired"
    TLP_NOT_ALLOWED = "tlp_not_allowed"
    SCORE_BELOW_MINIMUM = "score_below_minimum"
    CONFIDENCE_BELOW_MINIMUM = "confidence_below_minimum"
    DETECTION_REQUIRED = "detection_required"
    UNCLASSIFIED = "unclassified"
    SUBTYPE_NOT_ALLOWED = "subtype_not_allowed"

    # Razones relacionadas con deduplicacion/reenvio del mismo IOC.
    DUPLICATE_EVENT = "duplicate_event"
    DUPLICATE_CONTENT = "duplicate_content"
    ALREADY_DELIVERED = "already_delivered"
    SAME_VALUE_NEW_VERSION = "same_value_new_version"

    # Omitido por limite de capacidad/throughput del destino, no por una
    # regla de contenido.
    SKIPPED_CAPACITY = "skipped_capacity"

    # Descartado con motivo obligatorio: a diferencia de un rechazo de
    # politica, esta decision la toma un operador humano, no el motor de reglas.
    DISCARDED = "discarded"


class PolicyDecision(BaseModel):
    outcome: PolicyOutcome
    reason: ReasonCode = ReasonCode.OK
    policy_version: int
