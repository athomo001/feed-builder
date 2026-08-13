"""Un intento de entrega hacia un adapter, con circuit breaker y contador de
intentos hacia dead-letter (spec/09 Entrega 2 "Retries, circuit breaker y
dead-letter"; spec/03 "Reintentos con backoff y jitter... Circuit breaker
por destino... Dead-letter para intervencion manual").

Limite de alcance explicito: no existe cola/workers todavia (spec/03
"Queue y workers" sigue "no iniciado" en spec/PROJECT-MAP.md). Cada llamada
a `deliver` hace UN intento y actualiza el ledger; no reintenta sola ni
duerme en el hot path del Live Stream. El primer intento lo dispara
`hub/pipeline.py` al procesar un evento; los siguientes los dispara un
operador (o automatizacion externa) via `POST /deliveries/{id}/retry`.
`hub/retry.compute_backoff_seconds` le da a quien llama un numero de
segundos de espera sugerido antes del proximo intento -- no se aplica solo.
"""
from datetime import datetime, timezone
from typing import Optional

from hub.delivery import DeliveryState
from hub.ledger import LedgerEntry, get_delivery, upsert_delivery
from hub.models import CanonicalIOCEvent
from hub.policy import ReasonCode
from hub.retry import CircuitBreaker


def deliver(
    *,
    ledger_conn,
    event: CanonicalIOCEvent,
    destination_id: str,
    policy_version: int,
    adapter,
    max_attempts: int,
    circuit_breaker: Optional[CircuitBreaker] = None,
    idempotency_key: Optional[str] = None,
    reason: ReasonCode = ReasonCode.OK,
    now: Optional[datetime] = None,
) -> LedgerEntry:
    now = now or datetime.now(timezone.utc)
    idempotency_key = idempotency_key or f"{event.event_id}:{destination_id}"

    existing = get_delivery(ledger_conn, event.event_id, destination_id, policy_version)
    created_at = existing.created_at if existing else now
    attempts = (existing.attempts if existing else 0) + 1

    def record(*, state: DeliveryState, error: Optional[str] = None) -> LedgerEntry:
        entry = LedgerEntry(
            event_id=event.event_id,
            stix_id=event.stix_id,
            destination_id=destination_id,
            policy_version=policy_version,
            state=state,
            reason=reason if state == DeliveryState.DELIVERED else ReasonCode.OK,
            created_at=created_at,
            updated_at=now,
            error=error,
            attempts=attempts,
        )
        upsert_delivery(ledger_conn, entry)
        return entry

    if circuit_breaker is not None and not circuit_breaker.allow():
        return record(state=DeliveryState.RETRYING, error="circuit_open")

    rendered = adapter.render(event)
    result = adapter.send(rendered, idempotency_key=idempotency_key)

    if result.success:
        if circuit_breaker is not None:
            circuit_breaker.record_success()
        entry = record(state=DeliveryState.DELIVERED)
        adapter.acknowledge(result)
        return entry

    if circuit_breaker is not None:
        circuit_breaker.record_failure()

    state = DeliveryState.DEAD_LETTER if attempts >= max_attempts else DeliveryState.RETRYING
    return record(state=state, error=result.detail)
