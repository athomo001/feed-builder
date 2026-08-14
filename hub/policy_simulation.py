"""Simulacion de politica: ejecuta una muestra historica representativa y
compara el volumen resultante (aceptado/rechazado/revocado) entre la
politica activa y una candidata, con ejemplos concretos y una alerta si el
cambio supera un umbral configurado.

Dos modos, mismo motor (`hub/policy_engine.evaluate`): si el caller pasa
`sample_envelopes`, se evalua offline sin red (tests, o un operador
probando con datos propios). Si no, se trae una muestra acotada de OpenCTI
reusando `hub/backfill.run_backfill` -- no es un mecanismo de muestreo
nuevo, es el mismo backfill usado de forma no destructiva (solo junta
envelopes, no los procesa contra el ledger).

Autor: Athan Espinoza
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from hub.backfill import run_backfill
from hub.graphql_client import GraphQLClient
from hub.models import CanonicalIOCEvent
from hub.normalize import UnclassifiedIndicatorError, normalize_stix_indicator
from hub.policy_engine import evaluate
from hub.policy_store import PolicyVersion

# Umbral de alerta y limite de ejemplos: valores por defecto pensados para
# que el reporte destaque cambios significativos sin saturar al operador con
# el detalle completo de la muestra.
THRESHOLD_ALERT_PCT = 20.0
MAX_EXAMPLES = 5


@dataclass
class _Tally:
    accepted: int = 0
    rejected: int = 0
    revoked: int = 0
    examples: list = field(default_factory=list)


def _tally(events: list[CanonicalIOCEvent], policy: Optional[PolicyVersion], *, now: datetime) -> _Tally:
    tally = _Tally()
    # Sin politica activa (ej. primera publicacion de un destino) no hay un
    # "before" real con que comparar; se devuelve un tally vacio en vez de
    # fallar, para que la comparacion siga siendo posible.
    if policy is None:
        return tally
    for event in events:
        decision = evaluate(event, policy, now=now)
        if decision.outcome.value == "accepted":
            tally.accepted += 1
        elif decision.outcome.value == "revoked":
            tally.revoked += 1
        else:
            tally.rejected += 1
        if len(tally.examples) < MAX_EXAMPLES:
            tally.examples.append(
                {
                    "stix_id": event.stix_id,
                    "family": event.family.value,
                    "subtype": event.subtype,
                    "outcome": decision.outcome.value,
                    "reason": decision.reason.value,
                }
            )
    return tally


def _envelopes_to_events(envelopes: list[dict]) -> list[CanonicalIOCEvent]:
    events = []
    for i, envelope in enumerate(envelopes):
        try:
            events.append(normalize_stix_indicator(envelope, event_id=f"sim-{i}", source_id="simulation"))
        except (UnclassifiedIndicatorError, ValueError, KeyError):
            continue  # muestra representativa: un IOC no clasificable se omite, no rompe la simulacion
    return events


def simulate(
    *,
    candidate: PolicyVersion,
    active: Optional[PolicyVersion],
    sample_envelopes: Optional[list[dict]] = None,
    graphql_client: Optional[GraphQLClient] = None,
    sample_size: int = 50,
    now: Optional[datetime] = None,
) -> dict:
    now = now or datetime.now(timezone.utc)

    # Modo offline (sample_envelopes explicito) vs modo en vivo (se trae una
    # muestra reciente de OpenCTI): el mismo `_tally`/`evaluate` de abajo se
    # usa en ambos, solo cambia de donde salen los eventos a evaluar.
    if sample_envelopes is not None:
        events = _envelopes_to_events(sample_envelopes)
    else:
        if graphql_client is None:
            raise ValueError("se necesita 'sample' en el body o un graphql_client para muestrear OpenCTI en vivo")
        collected: list[dict] = []
        run_backfill(
            graphql_client,
            since=now - timedelta(days=7),
            max_pages=1,
            page_size=sample_size,
            on_envelope=collected.append,
        )
        events = _envelopes_to_events(collected)

    before = _tally(events, active, now=now)
    after = _tally(events, candidate, now=now)

    delta_pct = None
    # Guard contra division por cero: si la politica activa no acepto nada en
    # la muestra, un "% de cambio" no tiene una base valida para calcularse.
    if before.accepted > 0:
        delta_pct = round((after.accepted - before.accepted) / before.accepted * 100, 1)

    return {
        "sample_size": len(events),
        "before": {"accepted": before.accepted, "rejected": before.rejected, "revoked": before.revoked},
        "after": {
            "accepted": after.accepted,
            "rejected": after.rejected,
            "revoked": after.revoked,
            "examples": after.examples,
        },
        "delta_pct": delta_pct,
        "threshold_alert": delta_pct is not None and abs(delta_pct) >= THRESHOLD_ALERT_PCT,
    }
