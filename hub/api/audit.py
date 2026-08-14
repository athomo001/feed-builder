"""Helper de router para dejar una entrada de auditoria (spec/08-API-SECURITY.md
"Auditoria"). Junta el actor (`request.state.actor_token_id`, dejado por
`hub/api/auth.require_role`) y el `correlation_id` de la request con
`hub/api/audit_store.record`, para no repetir ese cableado en cada endpoint.
"""
from typing import Optional

from fastapi import Request

from hub.api.audit_store import AuditEntry, Result, record
from hub.api.deps import APIState
from hub.api.errors import correlation_id as get_correlation_id
from hub.api.token_store import APIToken


def write_audit(
    request: Request,
    state: APIState,
    *,
    actor: APIToken,
    action: str,
    resource_type: str,
    resource_id: str,
    before: Optional[dict] = None,
    after: Optional[dict] = None,
    reason: Optional[str] = None,
    result: Result = "success",
) -> AuditEntry:
    return record(
        state.audit_conn,
        actor_token_id=actor.token_id,
        actor_role=actor.role,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        before=before,
        after=after,
        reason=reason,
        result=result,
        correlation_id=get_correlation_id(request),
    )
