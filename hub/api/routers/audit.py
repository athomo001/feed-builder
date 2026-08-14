"""spec/07-ADMIN-UI-ANGULAR.md "Auditoria & Configuracion"; spec/08 roles
("viewer: lectura de estado y auditoria permitida").
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
