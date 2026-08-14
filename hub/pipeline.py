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
OpenCTI en si, no sobre a que destino se vaya a entregar.

Autor: Athan Espinoza
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from hub.dedup import classify_duplicate, content_key, content_version_key
from hub.delivery import DeliveryState
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


def _record(ledger_conn, event: CanonicalIOCEvent, destination_id: str, *, state, reason, now) -> LedgerEntry:
    # Construccion compartida de LedgerEntry para los caminos que nunca
    # llegan a intentar una entrega real (duplicado bloqueante, sin politica
    # activa, revocado o expirado) y por eso no pasan por `deliver()`.
    entry = LedgerEntry(
        event_id=event.event_id,
        stix_id=event.stix_id,
        destination_id=destination_id,
        policy_version=0,
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
        # Se resuelve la version activa por destino en cada evento (no se
        # cachea) porque un operador puede publicar una nueva politica en
        # cualquier momento y el proximo evento debe evaluarse contra ella.
        policy = get_active_version_for_destination(policies_conn, destination.destination_id)
        if policy is None:
            # Sin politica publicada y activa, el destino simplemente no
            # participa: una politica es obligatoria antes de poder activar
            # un destino, para evitar entregas sin reglas definidas.
            continue

        with span("policy.evaluate", event_id=event.event_id, stix_id=event.stix_id):
            decision = evaluate_policy(event, policy, default_ttl_days=default_ttl_days, now=now)
        adapter = adapters.get(destination.destination_id)

        if decision.outcome is PolicyOutcome.ACCEPTED:
            entry = deliver(
                ledger_conn=ledger_conn,
                event=event,
                destination_id=destination.destination_id,
                policy_version=policy.version,
                adapter=adapter,
                max_attempts=destination.retry.max_attempts,
                circuit_breaker=circuit_breakers.get(destination.destination_id),
                reason=dup_reason or ReasonCode.OK,
                now=now,
            )
            entries.append(entry)
            continue

        state = DeliveryState.REVOKED if decision.outcome is PolicyOutcome.REVOKED else (
            DeliveryState.EXPIRED if decision.reason is ReasonCode.EXPIRED else DeliveryState.SKIPPED
        )
        if adapter is not None:
            try:
                adapter.discard(event)
            except Exception:
                pass  # best-effort: no bloquear el ledger por un fallo de discard
        entry = _record(ledger_conn, event, destination.destination_id, state=state, reason=decision.reason, now=now)
        entries.append(entry)

    return PipelineResult(event=event, ledger_entries=entries)
