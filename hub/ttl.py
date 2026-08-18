"""Expiracion efectiva de un IOC.

La expiracion efectiva es el minimo de: valid_until del objeto (si existe),
created_at/modified_at mas TTL de politica, TTL maximo del destino, ventana
global de retencion. Se usa modified_at como base cuando esta disponible
(refleja el ultimo estado conocido, incluyendo renovaciones por update) y se
cae a created_at si no hay modified_at.

Autor: Athan Espinoza
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

    # El minimo entre todos los limites aplicables: cualquiera de ellos puede
    # volver invalido el IOC antes que los demas, y el mas restrictivo debe
    # ganar (nunca se sirve un IOC mas alla del limite mas corto).
    return min(candidates)


def effective_expiration_for_policy(event: CanonicalIOCEvent, ttl_days_by_subtype: dict) -> Optional[datetime]:
    """Como `effective_expiration`, pero `None` si la politica no declaro un
    TTL propio para este subtipo, en vez de asumir el default global de
    config -- los adapters file_feed (ver hub/adapters/factory.py) solo
    reciben la politica activa, no la config completa del Hub, asi que un
    subtipo sin TTL propio en la politica simplemente no vence solo (sigue
    sujeto al `discard()` reactivo de siempre cuando llegue un evento nuevo).
    Usado para completar `meta["valid_until"]` al escribir a un feed
    materializado (hub/txt_feed.py::FeedWriter), que es lo que le permite a
    `FeedWriter.rebuild()` vencer entradas solas, con el tiempo, sin esperar
    un evento nuevo para ese IOC puntual."""
    ttl_days = ttl_days_by_subtype.get(event.subtype)
    if ttl_days is None:
        return None
    return effective_expiration(event, policy_ttl_days=ttl_days)


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
