"""Orquestacion de un evento contra TODOS los destinos configurados.

Cada evento se evalua contra la politica ACTIVA publicada de cada destino
habilitado (`hub.policy_engine.evaluate`), y cada combinacion evento x
destino produce su propia fila de ledger
(`event_id + destination_id + policy_version`, el diseno de
`hub/ledger.py`), de forma que un mismo evento puede aceptarse en un
destino y rechazarse o expirar en otro sin que un resultado interfiera con
el otro.

Dedup (`event_id`/`content_version`/`same_value`) sigue siendo a nivel de
evento, no por destino: esas capas de duplicacion operan sobre el objeto
OpenCTI en si, no sobre a que destino se vaya a entregar. Consecuencia real
(reportada por el operador, 2026-08-18): un destino asignado a una politica
que YA tenia datos nunca recibe lo que otro destino ya proceso, porque ese
evento queda "visto" globalmente. `process_envelope_for_destination` es la
valvula de escape para ese caso puntual -- ver `hub/resync.py`.

Autor: Athan Espinoza
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from hub.dedup import classify_duplicate, content_key, content_version_key
from hub.delivery import DeliveryState
from hub.delivery_queue_store import allowed_sends_this_minute, count_pending, enqueue, record_sends
from hub.delivery_runner import deliver
from hub.destinations_store import Destination
from hub.ledger import LedgerEntry, upsert_delivery
from hub.models import CanonicalIOCEvent
from hub.normalize import normalize_stix_indicator
from hub.policy import PolicyOutcome, ReasonCode
from hub.policy_engine import evaluate as evaluate_policy
from hub.policy_store import get_active_version_for_destination
from hub.tracing import span

# DUPLICATE_EVENT/DUPLICATE_CONTENT son reintentos reales del mismo dato y
# se descartan sin llegar a evaluar politica. SAME_VALUE_NEW_VERSION es una
# actualizacion legitima con el mismo valor textual -- nunca se debe
# ocultar -- asi que NO esta en este set: se anota como razon pero el
# evento sigue el flujo normal de evaluacion por destino.
_BLOCKING_DUPLICATE_REASONS = {ReasonCode.DUPLICATE_EVENT, ReasonCode.DUPLICATE_CONTENT}


@dataclass
class DedupState:
    # Tres colecciones separadas porque hay tres capas de deduplicacion
    # distintas (evento exacto ya visto, nueva version de un stix_id ya
    # visto, mismo valor normalizado visto antes con otra version); mezclar
    # cualquiera de ellas rompe la clasificacion en `classify_duplicate`.
    seen_event_ids: set = field(default_factory=set)
    seen_content_versions: set = field(default_factory=set)
    seen_content_values: dict = field(default_factory=dict)


@dataclass
class PipelineResult:
    event: CanonicalIOCEvent
    ledger_entries: list[LedgerEntry] = field(default_factory=list)


def _record(ledger_conn, event: CanonicalIOCEvent, destination_id: str, *, state, reason, now, policy_version=0) -> LedgerEntry:
    # Construccion compartida de LedgerEntry para los caminos que no llaman a
    # `deliver()` directamente: duplicado bloqueante, sin politica activa,
    # revocado/expirado (policy_version=0, nunca hubo una version real
    # evaluada) o encolado por rate limit (policy_version real: `deliver()`
    # va a reusar esta misma fila cuando el drain lo procese, ver
    # `hub/service.py::drain_delivery_queues`).
    entry = LedgerEntry(
        event_id=event.event_id,
        stix_id=event.stix_id,
        destination_id=destination_id,
        policy_version=policy_version,
        state=state,
        reason=reason,
        created_at=now,
        updated_at=now,
    )
    upsert_delivery(ledger_conn, entry)
    return entry


def process_envelope(
    envelope: dict,
    *,
    event_id: str,
    source_id: str,
    dedup_state: DedupState,
    destinations: list[Destination],
    policies_conn,
    adapters: dict,
    ledger_conn,
    circuit_breakers: Optional[dict] = None,
    default_ttl_days: int = 30,
    now: Optional[datetime] = None,
    delivery_queue_conn=None,
) -> PipelineResult:
    now = now or datetime.now(timezone.utc)
    circuit_breakers = circuit_breakers if circuit_breakers is not None else {}
    # Normalizar primero: la clasificacion de duplicados y el resto del
    # flujo necesitan los campos canonicos (stix_id, modified_at, valor
    # normalizado), no el envelope crudo de OpenCTI.
    with span("opencti.event.normalize", event_id=event_id):
        event = normalize_stix_indicator(envelope, event_id=event_id, source_id=source_id)

    dup_reason = classify_duplicate(
        event,
        seen_event_ids=dedup_state.seen_event_ids,
        seen_content_versions=dedup_state.seen_content_versions,
        seen_content_values=dedup_state.seen_content_values,
    )
    dedup_state.seen_event_ids.add(event.event_id)

    if dup_reason in _BLOCKING_DUPLICATE_REASONS:
        entries = [
            _record(ledger_conn, event, d.destination_id, state=DeliveryState.SKIPPED, reason=dup_reason, now=now)
            for d in destinations
        ]
        return PipelineResult(event=event, ledger_entries=entries)

    # None (genuinamente nuevo) o SAME_VALUE_NEW_VERSION (misma huella,
    # version nueva): en ambos casos se registra la nueva version vista y se
    # sigue evaluando politica por destino.
    version_key = content_version_key(event.stix_id, event.modified_at)
    c_key = content_key(event.family, event.subtype, event.normalized_value)
    dedup_state.seen_content_versions.add(version_key)
    dedup_state.seen_content_values[c_key] = version_key

    entries: list[LedgerEntry] = []

    for destination in destinations:
        entry = _process_for_destination(
            event, destination, dup_reason=dup_reason,
            policies_conn=policies_conn, adapters=adapters, ledger_conn=ledger_conn,
            circuit_breakers=circuit_breakers, default_ttl_days=default_ttl_days,
            now=now, delivery_queue_conn=delivery_queue_conn,
        )
        if entry is not None:
            entries.append(entry)

    return PipelineResult(event=event, ledger_entries=entries)


def _process_for_destination(
    event: CanonicalIOCEvent,
    destination: Destination,
    *,
    dup_reason: Optional[ReasonCode],
    policies_conn,
    adapters: dict,
    ledger_conn,
    circuit_breakers: dict,
    default_ttl_days: int,
    now: datetime,
    delivery_queue_conn,
) -> Optional[LedgerEntry]:
    """Evalua un evento ya normalizado contra UN destino. Factorizado fuera
    de `process_envelope` para poder reusarse desde `hub/resync.py` (poner
    al dia un destino recien asignado a una politica que ya tenia datos, sin
    pasar por el deduplicado global -- ver docstring de ese modulo)."""
    # Se resuelve la version activa por destino en cada evento (no se
    # cachea) porque un operador puede publicar una nueva politica en
    # cualquier momento y el proximo evento debe evaluarse contra ella.
    policy = get_active_version_for_destination(policies_conn, destination.destination_id)
    if policy is None:
        # Sin politica publicada y activa, el destino simplemente no
        # participa: una politica es obligatoria antes de poder activar
        # un destino, para evitar entregas sin reglas definidas.
        return None

    with span("policy.evaluate", event_id=event.event_id, stix_id=event.stix_id):
        decision = evaluate_policy(event, policy, default_ttl_days=default_ttl_days, now=now)
    adapter = adapters.get(destination.destination_id)

    if decision.outcome is PolicyOutcome.ACCEPTED:
        reason = dup_reason or ReasonCode.OK
        rate_limit = destination.capacity.get("rate_limit_per_minute", 0) if destination.capacity else 0

        # spec/04 "Capacidad y throughput por destino": destinos api_push
        # limitan por tasa, no por capacidad de archivo -- "el worker
        # respeta el limite y encola el excedente; nunca lo descarta".
        # Se encola (en vez de entregar ya) si el destino ya tiene algo
        # esperando (preserva orden FIFO: un evento nuevo no puede pasar
        # adelante de lo que ya esta encolado) o si la ventana de este
        # minuto ya se agoto.
        if rate_limit and delivery_queue_conn is not None:
            already_queued = count_pending(delivery_queue_conn, destination.destination_id) > 0
            allowed_now = allowed_sends_this_minute(
                delivery_queue_conn, destination.destination_id, rate_limit_per_minute=rate_limit, now=now
            )
            if already_queued or allowed_now <= 0:
                enqueue(
                    delivery_queue_conn,
                    destination_id=destination.destination_id,
                    policy_version=policy.version,
                    reason=reason,
                    event=event,
                    now=now,
                )
                return _record(
                    ledger_conn, event, destination.destination_id,
                    state=DeliveryState.PENDING, reason=reason, now=now, policy_version=policy.version,
                )
            record_sends(delivery_queue_conn, destination.destination_id, 1, now=now)

        return deliver(
            ledger_conn=ledger_conn,
            event=event,
            destination_id=destination.destination_id,
            policy_version=policy.version,
            adapter=adapter,
            max_attempts=destination.retry.max_attempts,
            circuit_breaker=circuit_breakers.get(destination.destination_id),
            reason=reason,
            now=now,
        )

    state = DeliveryState.REVOKED if decision.outcome is PolicyOutcome.REVOKED else (
        DeliveryState.EXPIRED if decision.reason is ReasonCode.EXPIRED else DeliveryState.SKIPPED
    )
    if adapter is not None:
        try:
            adapter.discard(event)
        except Exception:
            pass  # best-effort: no bloquear el ledger por un fallo de discard
    return _record(ledger_conn, event, destination.destination_id, state=state, reason=decision.reason, now=now)


def process_envelope_for_destination(
    envelope: dict,
    *,
    event_id: str,
    source_id: str,
    destination: Destination,
    policies_conn,
    adapters: dict,
    ledger_conn,
    circuit_breakers: Optional[dict] = None,
    default_ttl_days: int = 30,
    now: Optional[datetime] = None,
    delivery_queue_conn=None,
) -> Optional[LedgerEntry]:
    """Evalua un envelope contra UN SOLO destino, sin pasar por el
    deduplicado global de `process_envelope` (`dedup_state`/`seen_event_ids`)
    -- ese deduplicado es POR EVENTO, no por destino, asi que un IOC ya
    entregado a otro destino nunca se re-evaluaba para uno nuevo, aunque ese
    destino en particular nunca lo hubiera recibido. Usado exclusivamente
    por `hub/resync.py` para poner al dia un destino recien asignado a una
    politica que ya tenia datos (bug real reportado por el operador,
    2026-08-18: agrego un destino Wazuh a una politica que ya alimentaba un
    TXT hacia horas, y el Wazuh se quedo pegado en 1 entrada por subtipo)."""
    now = now or datetime.now(timezone.utc)
    circuit_breakers = circuit_breakers if circuit_breakers is not None else {}
    with span("opencti.event.normalize", event_id=event_id):
        event = normalize_stix_indicator(envelope, event_id=event_id, source_id=source_id)
    return _process_for_destination(
        event, destination, dup_reason=None,
        policies_conn=policies_conn, adapters=adapters, ledger_conn=ledger_conn,
        circuit_breakers=circuit_breakers, default_ttl_days=default_ttl_days,
        now=now, delivery_queue_conn=delivery_queue_conn,
    )
