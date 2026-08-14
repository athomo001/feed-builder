"""Un intento de entrega hacia un adapter, con circuit breaker y contador de
intentos hacia dead-letter.

Limite de alcance explicito: no existe cola/workers todavia. Cada llamada a
`deliver` hace UN intento y actualiza el ledger; no reintenta sola ni
duerme en el hot path del Live Stream, para no bloquear la ingesta de
eventos nuevos mientras se reintenta una entrega fallida. El primer intento
lo dispara `hub/pipeline.py` al procesar un evento; los siguientes los
dispara un operador (o automatizacion externa) via
`POST /deliveries/{id}/retry`. `hub/retry.compute_backoff_seconds` le da a
quien llama un numero de segundos de espera sugerido antes del proximo
intento -- no se aplica solo.

Autor: Athan Espinoza
"""
from datetime import datetime, timezone
from typing import Optional

from hub.delivery import DeliveryState
from hub.ledger import LedgerEntry, get_delivery, upsert_delivery
from hub.models import CanonicalIOCEvent
from hub.policy import ReasonCode
from hub.retry import CircuitBreaker
from hub.tracing import span


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
    # Clave de idempotencia por defecto: evento + destino, para que el
    # adapter (o el sistema del otro lado) pueda detectar un reintento del
    # mismo dato aunque este helper no sepa nada del transporte concreto.
    idempotency_key = idempotency_key or f"{event.event_id}:{destination_id}"
    delivery_id = f"{event.event_id}::{destination_id}::{policy_version}"

    # La fila de ledger es por (evento, destino, version de politica): si ya
    # existe una entrega previa para esa misma combinacion, se conserva su
    # created_at y se suma un intento en vez de tratarla como nueva.
    existing = get_delivery(ledger_conn, event.event_id, destination_id, policy_version)
    created_at = existing.created_at if existing else now
    attempts = (existing.attempts if existing else 0) + 1

    # Closure sobre event/destination_id/policy_version/created_at/attempts:
    # evita repetir esos campos en cada uno de los puntos de salida de la
    # funcion (circuit abierto, exito, fallo).
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
        # Circuito abierto: el destino viene fallando, asi que ni se intenta
        # render/send -- se marca como RETRYING de una vez para no gastar
        # tiempo/recursos en un intento que muy probablemente va a fallar de
        # nuevo, y para no seguir golpeando un destino que puede estar caido.
        return record(state=DeliveryState.RETRYING, error="circuit_open")

    # Tres spans separados (render/send/acknowledge) en vez de uno solo:
    # render es CPU local y rapido, send es red y puede fallar/tardar,
    # acknowledge es post-procesamiento -- separarlos deja ver en las trazas
    # cual etapa es la lenta o la que esta fallando.
    with span("delivery.render", event_id=event.event_id, stix_id=event.stix_id, delivery_id=delivery_id):
        rendered = adapter.render(event)
    with span("delivery.send", event_id=event.event_id, stix_id=event.stix_id, delivery_id=delivery_id):
        result = adapter.send(rendered, idempotency_key=idempotency_key)

    if result.success:
        if circuit_breaker is not None:
            circuit_breaker.record_success()
        entry = record(state=DeliveryState.DELIVERED)
        with span("delivery.acknowledge", event_id=event.event_id, stix_id=event.stix_id, delivery_id=delivery_id):
            adapter.acknowledge(result)
        return entry

    if circuit_breaker is not None:
        circuit_breaker.record_failure()

    # Tras max_attempts se deja de reintentar automaticamente y se manda a
    # dead-letter: evita reintentos infinitos contra un destino roto y deja
    # la decision de que hacer con ese evento a un operador humano.
    state = DeliveryState.DEAD_LETTER if attempts >= max_attempts else DeliveryState.RETRYING
    return record(state=state, error=result.detail)
