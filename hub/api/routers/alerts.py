"""spec/09-ROADMAP-ACCEPTANCE.md Entrega 4 "Alertas email/webhook";
spec/06-OBSERVABILITY.md seccion 5. Sin scheduler real (spec/03 "Queue y
workers" sigue pendiente): `POST /alerts/evaluate` se dispara manual o por
cron externo, mismo patron ya usado para `POST /deliveries/{id}/retry`.
`hub/service.py` ademas llama las reglas que dependen de estado in-process
(OpenCTI/cursor) una vez por iteracion de su propio loop.
"""
import os
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query, Request

from hub.alert_rules import (
    evaluate_cursor_not_advancing,
    evaluate_dead_letter_nonzero,
    evaluate_destination_delivery_stale,
    evaluate_feed_stale,
    evaluate_opencti_disconnected,
)
from hub.alerting import build_channels, notify_alerts
from hub.alerting_store import acknowledge_alert, list_alerts, resolve_alert, upsert_alert
from hub.api.audit import write_audit
from hub.api.auth import require_role
from hub.api.deps import APIState, get_state
from hub.api.errors import APIError
from hub.cursor_store import load_cursor
from hub.delivery import DeliveryState
from hub.destinations_store import list_destinations
from hub.ledger import list_dead_letters, search_deliveries
from hub.service import _heartbeat_path, heartbeat_age_seconds

router = APIRouter(prefix="/admin/api/v1/alerts")

# Pares (condition, component) fijos de las reglas de hub/alert_rules.py.
# Necesario para reconciliar incluso cuando una condicion deja de tener
# CUALQUIER candidato (por ejemplo el dead-letter de un destino se vacia
# del todo) -- si se derivara solo de los candidatos de esta corrida, una
# condicion sin candidatos nunca entraria al loop de abajo y su alerta
# quedaria firing para siempre en vez de resolverse.
_KNOWN_CONDITIONS = [
    ("opencti_disconnected", "ingestion"),
    ("cursor_not_advancing", "ingestion"),
    ("dead_letter_nonzero", "destination"),
    ("destination_delivery_stale", "destination"),
    ("feed_stale", "feed"),
]


def _feed_ages_seconds(state: APIState) -> dict:
    now = time.time()
    ages = {}
    for destination in list_destinations(state.destinations_conn, enabled=True):
        feed_dir = os.path.join(state.config.txt_feed_dir, destination.destination_id)
        if not os.path.isdir(feed_dir):
            continue
        for name in os.listdir(feed_dir):
            if name.startswith(".") or name.endswith(".tmp"):
                continue
            path = os.path.join(feed_dir, name)
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                continue
            ages[f"{destination.destination_id}::{name}"] = now - mtime
    return ages


def _last_success_seconds_ago(state: APIState) -> dict:
    now = datetime.now(timezone.utc)
    result = {}
    for destination in list_destinations(state.destinations_conn, enabled=True):
        delivered = search_deliveries(
            state.ledger_conn, destination_id=destination.destination_id, state=DeliveryState.DELIVERED, limit=1
        )
        result[destination.destination_id] = (now - delivered[0].updated_at).total_seconds() if delivered else None
    return result


def _evaluate_candidates(state: APIState) -> list:
    heartbeat_age = heartbeat_age_seconds(_heartbeat_path(state.config))
    cursor = load_cursor(state.cursor_conn, state.config.source_id)
    cursor_age = (datetime.now(timezone.utc) - cursor.updated_at).total_seconds() if cursor else None

    candidates = []
    candidates += evaluate_opencti_disconnected(heartbeat_age, max_age_seconds=state.config.alert_opencti_disconnected_seconds)
    candidates += evaluate_cursor_not_advancing(cursor_age, max_unchanged_seconds=state.config.alert_cursor_stale_seconds)
    candidates += evaluate_dead_letter_nonzero(list_dead_letters(state.ledger_conn))
    candidates += evaluate_destination_delivery_stale(
        _last_success_seconds_ago(state), max_age_seconds=state.config.alert_destination_stale_seconds
    )
    candidates += evaluate_feed_stale(_feed_ages_seconds(state), max_age_seconds=state.config.alert_feed_stale_seconds)
    return candidates


def evaluate_and_notify(state: APIState) -> list:
    """Corre todas las reglas, reconcilia el estado persistido (nuevas
    alertas -> firing, condiciones que dejaron de cumplirse -> resolved) y
    notifica por los canales configurados que superan el cooldown y la
    severidad minima de ese canal. Reusado por el endpoint HTTP y por el
    tick periodico de `hub/service.py`."""
    conn = state.alerts_conn
    candidates = _evaluate_candidates(state)

    by_condition: dict = {key: [] for key in _KNOWN_CONDITIONS}
    for candidate in candidates:
        by_condition.setdefault((candidate.condition, candidate.component), []).append(candidate)

    active_alerts = []
    for (condition, component), group in by_condition.items():
        active_resource_ids = {c.resource_id for c in group}
        for candidate in group:
            alert = upsert_alert(
                conn,
                condition=candidate.condition,
                severity=candidate.severity,
                component=candidate.component,
                resource_id=candidate.resource_id,
                observed_value=candidate.observed_value,
            )
            active_alerts.append(alert)
        for existing in list_alerts(conn, condition=condition, component=component, state="firing"):
            if existing.resource_id not in active_resource_ids:
                resolve_alert(conn, existing.alert_id)

    notify_alerts(conn, active_alerts, build_channels(state.config), cooldown_seconds=state.config.alert_cooldown_seconds)
    return active_alerts


@router.get("")
def list_all(
    severity: str = Query(default=None),
    state_filter: str = Query(default=None, alias="state"),
    component: str = Query(default=None),
    state: APIState = Depends(get_state),
    _token=Depends(require_role("viewer")),
):
    return [
        a.model_dump(mode="json")
        for a in list_alerts(state.alerts_conn, severity=severity, state=state_filter, component=component, limit=200)
    ]


@router.post("/evaluate")
def evaluate(state: APIState = Depends(get_state), _token=Depends(require_role("operator"))):
    alerts = evaluate_and_notify(state)
    return [a.model_dump(mode="json") for a in alerts]


@router.post("/{alert_id}/acknowledge")
def acknowledge(
    alert_id: str,
    request: Request,
    state: APIState = Depends(get_state),
    token=Depends(require_role("operator")),
):
    updated = acknowledge_alert(state.alerts_conn, alert_id, actor=token.token_id)
    if updated is None:
        raise APIError(404, "Not Found", f"alerta '{alert_id}' no existe", error_code="alert_not_found")
    write_audit(
        request, state, actor=token, action="alert.acknowledge",
        resource_type="alert", resource_id=alert_id,
    )
    return updated.model_dump(mode="json")
