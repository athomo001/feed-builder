"""Expiracion efectiva de un IOC (spec/04-IOC-MODEL-POLICIES.md "TTL y vigencia", Entrega 1).

"La expiracion efectiva es el minimo de: valid_until del objeto (si existe),
created_at/modified_at mas TTL de politica, TTL maximo del destino, ventana
global de retencion." Se usa modified_at como base cuando esta disponible
(refleja el ultimo estado conocido, incluyendo renovaciones por update) y se
cae a created_at si no hay modified_at.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

from hub.models import CanonicalIOCEvent


def effective_expiration(
    event: CanonicalIOCEvent,
    *,
    policy_ttl_days: int,
    destination_ttl_days: Optional[int] = None,
    retention_window_days: Optional[int] = None,
) -> datetime:
    base_ts = event.modified_at or event.created_at
    candidates = [base_ts + timedelta(days=policy_ttl_days)]

    if event.valid_until is not None:
        candidates.append(event.valid_until)
    if destination_ttl_days is not None:
        candidates.append(base_ts + timedelta(days=destination_ttl_days))
    if retention_window_days is not None:
        candidates.append(base_ts + timedelta(days=retention_window_days))

    return min(candidates)


def is_expired(
    event: CanonicalIOCEvent,
    *,
    policy_ttl_days: int,
    destination_ttl_days: Optional[int] = None,
    retention_window_days: Optional[int] = None,
    now: Optional[datetime] = None,
) -> bool:
    now = now or datetime.now(timezone.utc)
    expiration = effective_expiration(
        event,
        policy_ttl_days=policy_ttl_days,
        destination_ttl_days=destination_ttl_days,
        retention_window_days=retention_window_days,
    )
    return expiration < now
