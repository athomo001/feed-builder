"""Control de la ingesta desde OpenCTI: pausar/reanudar y reconciliar
requieren rol `operator`; rebobinar el cursor requiere `security-admin` y
un motivo obligatorio porque puede provocar reprocesar/reenviar IOCs ya
entregados. Este router solo escribe pedidos en `hub/ingestion_control.py`;
el proceso `hub.service` los aplica en su propio loop, ya que la API y la
ingesta corren en procesos separados (ver ese modulo para el porque).

Autor: Athan Espinoza
"""
from fastapi import APIRouter, Depends, Request

from hub.api.audit import write_audit
from hub.api.auth import require_role
from hub.api.deps import APIState, get_state
from hub.api.schemas import RewindRequest
from hub.cursor_store import load_cursor
from hub.ingestion_control import get_control, request_reconcile, request_rewind, set_paused
from hub.service import _heartbeat_path, heartbeat_age_seconds

router = APIRouter(prefix="/admin/api/v1/ingestion")


@router.get("/status")
def status(state: APIState = Depends(get_state), _token=Depends(require_role("viewer"))):
    control = get_control(state.ingestion_control_conn, state.config.source_id)
    cursor = load_cursor(state.cursor_conn, state.config.source_id)
    return {
        "source_id": state.config.source_id,
        "paused": control.paused,
        "reconcile_requested": control.reconcile_requested,
        "rewind_pending": control.rewind_to_cursor is not None,
        "cursor_value": cursor.cursor_value if cursor else None,
        "cursor_updated_at": cursor.updated_at.isoformat() if cursor else None,
        "heartbeat_age_seconds": heartbeat_age_seconds(_heartbeat_path(state.config)),
    }


@router.post("/pause")
def pause(request: Request, state: APIState = Depends(get_state), token=Depends(require_role("operator"))):
    control = set_paused(state.ingestion_control_conn, state.config.source_id, True)
    write_audit(
        request, state, actor=token, action="ingestion.pause",
        resource_type="ingestion", resource_id=state.config.source_id,
    )
    return control.model_dump(mode="json")


@router.post("/resume")
def resume(request: Request, state: APIState = Depends(get_state), token=Depends(require_role("operator"))):
    control = set_paused(state.ingestion_control_conn, state.config.source_id, False)
    write_audit(
        request, state, actor=token, action="ingestion.resume",
        resource_type="ingestion", resource_id=state.config.source_id,
    )
    return control.model_dump(mode="json")


@router.post("/reconcile")
def reconcile(request: Request, state: APIState = Depends(get_state), token=Depends(require_role("operator"))):
    control = request_reconcile(state.ingestion_control_conn, state.config.source_id)
    write_audit(
        request, state, actor=token, action="ingestion.reconcile_requested",
        resource_type="ingestion", resource_id=state.config.source_id,
    )
    return control.model_dump(mode="json")


@router.post("/rewind")
def rewind(
    payload: RewindRequest,
    request: Request,
    state: APIState = Depends(get_state),
    token=Depends(require_role("security-admin")),
):
    # No se crea un checkpoint separado: la propia entrada de auditoria, con
    # el cursor actual como `before`, ya cumple ese rol y permite reconstruir
    # a donde apuntaba el cursor antes del rebobinado si hace falta revertir.
    current = load_cursor(state.cursor_conn, state.config.source_id)
    control = request_rewind(
        state.ingestion_control_conn,
        state.config.source_id,
        cursor_value=payload.cursor_value,
        reason=payload.reason,
    )
    write_audit(
        request, state, actor=token, action="ingestion.rewind_requested",
        resource_type="ingestion", resource_id=state.config.source_id,
        before={"cursor_value": current.cursor_value if current else None},
        after={"cursor_value": payload.cursor_value},
        reason=payload.reason,
    )
    return control.model_dump(mode="json")
