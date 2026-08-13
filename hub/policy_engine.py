"""Motor de politicas configurable (spec/03-ARCHITECTURE.md "Policy engine":
"Evalua politicas inmutables por version. Devuelve accepted/rejected/
skipped/revoked mas codigos de razon"; spec/04 "Filtros de seguridad").

Este es el hueco que Entrega 1 dejo marcado en spec/PROJECT-MAP.md ("falta:
evaluacion de reglas configurables"): `hub/pipeline.py` en Entrega 1 solo
aplicaba revoked/expirado/dedup con un TTL fijo por variable de entorno;
aqui la fuente de la regla es una `PolicyVersion` real, versionada,
publicada via `hub/policy_store.py`.
"""
from datetime import datetime
from typing import Optional

from hub.models import CanonicalIOCEvent
from hub.policy import PolicyDecision, PolicyOutcome, ReasonCode
from hub.policy_store import PolicyVersion
from hub.ttl import is_expired

DEFAULT_TTL_DAYS = 30


def _subtype_allowed(event: CanonicalIOCEvent, policy: PolicyVersion) -> bool:
    return any(
        allowed.family == event.family.value and event.subtype in allowed.subtypes
        for allowed in policy.allowed_iocs
    )


def evaluate(
    event: CanonicalIOCEvent,
    policy: PolicyVersion,
    *,
    default_ttl_days: int = DEFAULT_TTL_DAYS,
    now: Optional[datetime] = None,
) -> PolicyDecision:
    if event.revoked:
        return PolicyDecision(outcome=PolicyOutcome.REVOKED, reason=ReasonCode.REVOKED, policy_version=policy.version)

    if not _subtype_allowed(event, policy):
        return PolicyDecision(
            outcome=PolicyOutcome.REJECTED, reason=ReasonCode.SUBTYPE_NOT_ALLOWED, policy_version=policy.version
        )

    ttl_days = policy.ttl_days.get(event.subtype, default_ttl_days)
    if is_expired(event, policy_ttl_days=ttl_days, now=now):
        return PolicyDecision(outcome=PolicyOutcome.REJECTED, reason=ReasonCode.EXPIRED, policy_version=policy.version)

    return PolicyDecision(outcome=PolicyOutcome.ACCEPTED, reason=ReasonCode.OK, policy_version=policy.version)
