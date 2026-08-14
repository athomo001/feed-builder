"""Motor de politicas configurable: evalua politicas inmutables por version
y devuelve accepted/rejected/skipped/revoked mas codigos de razon.

Antes de este modulo, la evaluacion de politica solo aplicaba
revoked/expirado/dedup con un TTL fijo tomado de una variable de entorno.
Aqui la fuente de la regla es una `PolicyVersion` real, versionada y
publicada via `hub/policy_store.py`, para poder cambiar reglas sin redeploy
y para poder auditar que version decidio cada evento.

Autor: Athan Espinoza
"""
from datetime import datetime
from typing import Optional

from hub.models import CanonicalIOCEvent
from hub.policy import PolicyDecision, PolicyOutcome, ReasonCode
from hub.policy_store import PolicyVersion
from hub.ttl import is_expired

DEFAULT_TTL_DAYS = 30


def _subtype_allowed(event: CanonicalIOCEvent, policy: PolicyVersion) -> bool:
    # Un subtipo debe estar explicitamente permitido para su familia en esta
    # version de politica: no hay permiso implicito por familia completa.
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
    # Orden de evaluacion deliberado: revoked corta antes que cualquier otra
    # regla, porque un IOC revocado en origen nunca deberia enviarse sin
    # importar que otras reglas cumpliria.
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
