"""spec/08-API-SECURITY.md `/deliveries/{delivery_id}/retry|discard`, rol
`operator`. `delivery_id` no es un campo propio del ledger (la clave real
es `event_id + destination_id + policy_version`, spec/03); se codifica como
`{event_id}::{destination_id}::{policy_version}` para tener una sola ruta.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends

from hub.adapters.http_push_adapter import HttpPushAdapter
from hub.adapters.txt_feed_adapter import TxtFeedAdapter
from hub.api.auth import require_role
from hub.api.deps import APIState, get_state
from hub.api.errors import APIError
from hub.api.schemas import DiscardRequest
from hub.delivery import DeliveryState
from hub.delivery_runner import deliver
from hub.destinations_store import get_destination
from hub.graphql_indicator import GET_INDICATOR_QUERY, indicator_node_to_envelope
from hub.ledger import get_delivery, list_dead_letters, upsert_delivery
from hub.normalize import normalize_stix_indicator
from hub.policy import ReasonCode
from hub.retry import CircuitBreaker

router = APIRouter(prefix="/admin/api/v1/deliveries")

_SEP = "::"


def _parse_delivery_id(delivery_id: str) -> tuple[str, str, int]:
    parts = delivery_id.split(_SEP)
    if len(parts) != 3:
        raise APIError(
            400, "Bad Request", "delivery_id debe tener la forma 'event_id::destination_id::policy_version'",
            error_code="invalid_delivery_id",
        )
    event_id, destination_id, version_str = parts
    try:
        policy_version = int(version_str)
    except ValueError:
        raise APIError(400, "Bad Request", "policy_version en delivery_id debe ser entero", error_code="invalid_delivery_id")
    return event_id, destination_id, policy_version


@router.get("/dead-letters")
def dead_letters(state: APIState = Depends(get_state), _token=Depends(require_role("viewer"))):
    return [e.model_dump(mode="json") for e in list_dead_letters(state.ledger_conn)]


@router.post("/{delivery_id}/retry")
def retry(delivery_id: str, state: APIState = Depends(get_state), _token=Depends(require_role("operator"))):
    event_id, destination_id, policy_version = _parse_delivery_id(delivery_id)
    entry = get_delivery(state.ledger_conn, event_id, destination_id, policy_version)
    if entry is None:
        raise APIError(404, "Not Found", f"delivery '{delivery_id}' no existe", error_code="delivery_not_found")
    if entry.state not in (DeliveryState.RETRYING, DeliveryState.DEAD_LETTER):
        raise APIError(
            409, "Conflict", f"delivery esta en estado '{entry.state.value}', no es reintentable",
            error_code="not_retryable",
        )

    destination = get_destination(state.destinations_conn, destination_id)
    if destination is None:
        raise APIError(404, "Not Found", f"destination '{destination_id}' ya no existe", error_code="destination_not_found")

    try:
        data = state.graphql_client.query(GET_INDICATOR_QUERY, {"id": entry.stix_id})
    except Exception as e:
        raise APIError(502, "Bad Gateway", f"no se pudo consultar OpenCTI: {e}", error_code="opencti_unavailable")

    node = data.get("indicator")
    if node is None:
        raise APIError(
            502, "Bad Gateway", "OpenCTI no devolvio el indicador para reintentar", error_code="opencti_indicator_missing"
        )

    envelope = indicator_node_to_envelope(node, action="create")
    event = normalize_stix_indicator(envelope, event_id=event_id, source_id=state.config.source_id)

    if destination.adapter == "txt_feed":
        adapter = TxtFeedAdapter(destination, base_dir=state.config.txt_feed_dir)
        breaker = None
    else:
        adapter = HttpPushAdapter(destination)
        breaker = state.circuit_breakers.setdefault(destination_id, CircuitBreaker())

    updated = deliver(
        ledger_conn=state.ledger_conn,
        event=event,
        destination_id=destination_id,
        policy_version=policy_version,
        adapter=adapter,
        max_attempts=destination.retry.max_attempts,
        circuit_breaker=breaker,
    )
    return updated.model_dump(mode="json")


@router.post("/{delivery_id}/discard")
def discard(
    delivery_id: str,
    payload: DiscardRequest,
    state: APIState = Depends(get_state),
    _token=Depends(require_role("operator")),
):
    event_id, destination_id, policy_version = _parse_delivery_id(delivery_id)
    entry = get_delivery(state.ledger_conn, event_id, destination_id, policy_version)
    if entry is None:
        raise APIError(404, "Not Found", f"delivery '{delivery_id}' no existe", error_code="delivery_not_found")

    updated = entry.model_copy(
        update={
            "state": DeliveryState.SKIPPED,
            "reason": ReasonCode.DISCARDED,
            "error": payload.reason,
            "updated_at": datetime.now(timezone.utc),
        }
    )
    upsert_delivery(state.ledger_conn, updated)
    return updated.model_dump(mode="json")
