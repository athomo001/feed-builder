"""Endpoint de solo lectura sobre el log de auditoria. Requiere rol
`viewer` como el resto de endpoints de lectura, ya que el historial de
acciones no es mas sensible que el estado que ya se puede consultar.

Autor: Athan Espinoza
"""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query

from hub.api.audit_store import list_audit
from hub.api.auth import require_role
from hub.api.deps import APIState, get_state

router = APIRouter(prefix="/admin/api/v1/audit")


@router.get("")
def list_all(
    state: APIState = Depends(get_state),
    # Filtros todos opcionales para poder acotar la busqueda por cualquier
    # combinacion de actor/accion/recurso/fecha sin necesitar un endpoint
    # distinto por cada criterio.
    actor_token_id: Optional[str] = Query(default=None),
    action: Optional[str] = Query(default=None),
    resource_type: Optional[str] = Query(default=None),
    resource_id: Optional[str] = Query(default=None),
    since: Optional[datetime] = Query(default=None),
    until: Optional[datetime] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _token=Depends(require_role("viewer")),
):
    entries = list_audit(
        state.audit_conn,
        actor_token_id=actor_token_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        since=since,
        until=until,
        limit=limit,
        offset=offset,
    )
    return [e.model_dump(mode="json") for e in entries]
