"""Endpoints para reintentar o descartar entregas fallidas, con rol
`operator`. `delivery_id` no es un campo propio del ledger -- la clave real
de una entrega es `event_id + destination_id + policy_version` -- asi que
se codifica como `{event_id}::{destination_id}::{policy_version}` para
poder direccionar una entrega especifica con una sola ruta.

Autor: Athan Espinoza
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request

from hub.adapters.factory import build_adapter, uses_circuit_breaker
from hub.api.audit import write_audit
from hub.api.auth import require_role
from hub.api.deps import APIState, get_graphql_client, get_state
from hub.api.errors import APIError
from hub.api.schemas import DiscardRequest
from hub.delivery import DeliveryState
from hub.delivery_queue_store import count_pending, list_destinations_with_pending
from hub.delivery_runner import deliver
from hub.destinations_store import get_destination
from hub.graphql_indicator import GET_INDICATOR_QUERY, indicator_node_to_envelope
from hub.ledger import get_delivery, list_dead_letters, upsert_delivery
from hub.normalize import normalize_stix_indicator
from hub.policy import ReasonCode
from hub.policy_store import get_active_version_for_destination
from hub.retry import CircuitBreaker

router = APIRouter(prefix="/admin/api/v1/deliveries")

_SEP = "::"


def _parse_delivery_id(delivery_id: str) -> tuple[str, str, int]:
    # Se valida la forma completa (3 partes, version entera) antes de tocar
    # el ledger: un delivery_id malformado debe ser 400 del caller, no un
    # 404/500 confuso mas adelante por buscar con valores basura.
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


# spec/04 "El conteo de IOC excluidos por cupo... queda visible en la UI
# junto al feed/destino afectado, no solo en logs" -- mismo criterio de
# transparencia aplicado a lo que esta esperando turno por rate limit en vez
# de descartado por capacidad: un operador viendo un delivery en PENDING
# necesita poder confirmar que es "esperando su turno", no "se perdio".
@router.get("/queue")
def queue_depth(state: APIState = Depends(get_state), _token=Depends(require_role("viewer"))):
    return [
        {"destination_id": destination_id, "pending": count_pending(state.delivery_queue_conn, destination_id)}
        for destination_id in sorted(list_destinations_with_pending(state.delivery_queue_conn))
    ]


@router.post("/{delivery_id}/retry")
def retry(
    delivery_id: str,
    request: Request,
    state: APIState = Depends(get_state),
    graphql_client=Depends(get_graphql_client),
    token=Depends(require_role("operator")),
):
    event_id, destination_id, policy_version = _parse_delivery_id(delivery_id)
    entry = get_delivery(state.ledger_conn, event_id, destination_id, policy_version)
    if entry is None:
        raise APIError(404, "Not Found", f"delivery '{delivery_id}' no existe", error_code="delivery_not_found")
    # Solo tiene sentido reintentar algo que quedo en un estado de fallo
    # (retrying/dead-letter); reintentar un DELIVERED o SKIPPED reenviaria un
    # IOC que ya se entrego (o se descarto a proposito), de ahi el 409.
    if entry.state not in (DeliveryState.RETRYING, DeliveryState.DEAD_LETTER):
        raise APIError(
            409, "Conflict", f"delivery esta en estado '{entry.state.value}', no es reintentable",
            error_code="not_retryable",
        )

    destination = get_destination(state.destinations_conn, destination_id)
    if destination is None:
        raise APIError(404, "Not Found", f"destination '{destination_id}' ya no existe", error_code="destination_not_found")

    if graphql_client is None:
        raise APIError(409, "Conflict", "OpenCTI no esta configurado todavia", error_code="opencti_not_configured")

    # El ledger solo guarda el stix_id, no el contenido del indicador: hay
    # que volver a consultarlo a OpenCTI para reconstruir el evento a
    # entregar, ya que pudo cambiar desde el intento original.
    try:
        data = graphql_client.query(GET_INDICATOR_QUERY, {"id": entry.stix_id})
    except Exception as e:
        raise APIError(502, "Bad Gateway", f"no se pudo consultar OpenCTI: {e}", error_code="opencti_unavailable")

    node = data.get("indicator")
    if node is None:
        # El indicador pudo haber sido borrado/despublicado en OpenCTI entre
        # el fallo original y este reintento; 502 en vez de 404 porque la
        # falla es de la dependencia externa, no de este request.
        raise APIError(
            502, "Bad Gateway", "OpenCTI no devolvio el indicador para reintentar", error_code="opencti_indicator_missing"
        )

    envelope = indicator_node_to_envelope(node, action="create")
    event = normalize_stix_indicator(envelope, event_id=event_id, source_id=state.config.source_id)

    adapter = build_adapter(
        destination,
        txt_feed_dir=state.config.txt_feed_dir,
        taxii_conn=state.taxii_conn,
        secrets_conn=state.secrets_conn,
        cipher=state.secret_cipher,
        policy=get_active_version_for_destination(state.policies_conn, destination_id),
    )
    # El breaker se busca/crea por destination_id y se guarda en el estado
    # compartido de la app (no por request): asi el estado abierto/cerrado
    # persiste entre llamadas y refleja la salud real del destino a lo largo
    # del tiempo, no solo de este reintento puntual.
    breaker = state.circuit_breakers.setdefault(destination_id, CircuitBreaker()) if uses_circuit_breaker(destination) else None

    updated = deliver(
        ledger_conn=state.ledger_conn,
        event=event,
        destination_id=destination_id,
        policy_version=policy_version,
        adapter=adapter,
        max_attempts=destination.retry.max_attempts,
        circuit_breaker=breaker,
    )
    write_audit(
        request, state, actor=token, action="delivery.retry",
        resource_type="delivery", resource_id=delivery_id,
        before={"state": entry.state.value, "attempts": entry.attempts},
        after={"state": updated.state.value, "attempts": updated.attempts},
    )
    return updated.model_dump(mode="json")


@router.post("/{delivery_id}/discard")
def discard(
    delivery_id: str,
    payload: DiscardRequest,
    request: Request,
    state: APIState = Depends(get_state),
    token=Depends(require_role("operator")),
):
    event_id, destination_id, policy_version = _parse_delivery_id(delivery_id)
    entry = get_delivery(state.ledger_conn, event_id, destination_id, policy_version)
    if entry is None:
        raise APIError(404, "Not Found", f"delivery '{delivery_id}' no existe", error_code="delivery_not_found")

    # A diferencia de retry, discard no exige un estado previo especifico:
    # un operador puede decidir abandonar una entrega en cualquier estado de
    # fallo, y `reason` (obligatorio) queda auditado como justificacion.
    updated = entry.model_copy(
        update={
            "state": DeliveryState.SKIPPED,
            "reason": ReasonCode.DISCARDED,
            "error": payload.reason,
            "updated_at": datetime.now(timezone.utc),
        }
    )
    upsert_delivery(state.ledger_conn, updated)
    write_audit(
        request, state, actor=token, action="delivery.discard",
        resource_type="delivery", resource_id=delivery_id,
        before={"state": entry.state.value}, after={"state": updated.state.value},
        reason=payload.reason,
    )
    return updated.model_dump(mode="json")
