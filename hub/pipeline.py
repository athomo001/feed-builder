"""Orquestacion de un evento contra TODOS los destinos configurados
(spec/09-ROADMAP-ACCEPTANCE.md Entrega 1 "separar ingestion, politicas,
persistencia y escritura TXT"; Entrega 2 "CRUD de destinos y politicas").

Entrega 1 usaba un unico destino fijo (`txt-feed-default`) porque todavia
no existia CRUD de destinos ni politicas configurables -- quedo documentado
como solucion temporal en spec/PROJECT-MAP.md. Entrega 2 lo reemplaza: cada
evento se evalua contra la politica ACTIVA publicada de cada destino
habilitado (`hub.policy_engine.evaluate`), y cada combinacion evento x
destino sigue produciendo su propia fila de ledger
(`event_id + destination_id + policy_version`, el diseno original de
`hub/ledger.py` desde Entrega 0).

Dedup (`event_id`/`content_version`/`same_value`) sigue siendo a nivel de
evento, no por destino (spec/04 "Duplicacion" capas 1-2 son sobre el objeto
OpenCTI, no sobre el destino).
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

# spec/04 "Politica de duplicados": DUPLICATE_EVENT/DUPLICATE_CONTENT son
# reintentos reales y se descartan. SAME_VALUE_NEW_VERSION es una
# actualizacion legitima con el mismo valor textual -- "nunca se debe
# ocultar" -- asi que NO esta en este set: se anota pero sigue el flujo.
_BLOCKING_DUPLICATE_REASONS = {ReasonCode.DUPLICATE_EVENT, ReasonCode.DUPLICATE_CONTENT}


@dataclass
class DedupState:
    seen_event_ids: set = field(default_factory=set)
    seen_content_versions: set = field(default_factory=set)
    seen_content_values: dict = field(default_factory=dict)


@dataclass
class PipelineResult:
    event: CanonicalIOCEvent
    ledger_entries: list[LedgerEntry] = field(default_factory=list)


def _record(ledger_conn, event: CanonicalIOCEvent, destination_id: str, *, state, reason, now) -> LedgerEntry:
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
        policy = get_active_version_for_destination(policies_conn, destination.destination_id)
        if policy is None:
            # spec/08 "politica obligatoria antes de activar destino": sin
            # politica publicada, el destino simplemente no participa.
            continue

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
