"""Helper de router para dejar una entrada de auditoria. Junta el actor
(`request.state.actor_token_id`, dejado por `hub/api/auth.require_role`) y
el `correlation_id` de la request con `hub/api/audit_store.record`, para no
repetir ese cableado en cada endpoint.

`actor` puede ser un `APIToken` (token de servicio) o un
`hub.api.auth.AuthenticatedActor` (sesion OIDC) -- ambos exponen
`.token_id`/`.role`, este modulo no necesita distinguirlos.

Autor: Athan Espinoza
"""
from typing import Optional, Union

from fastapi import Request

from hub.api.audit_store import AuditEntry, Result, record
from hub.api.auth import AuthenticatedActor
from hub.api.deps import APIState
from hub.api.errors import correlation_id as get_correlation_id
from hub.api.token_store import APIToken


def write_audit(
    request: Request,
    state: APIState,
    *,
    actor: Union[APIToken, AuthenticatedActor],
    action: str,
    resource_type: str,
    resource_id: str,
    before: Optional[dict] = None,
    after: Optional[dict] = None,
    reason: Optional[str] = None,
    result: Result = "success",
) -> AuditEntry:
    # Firma amplia (before/after/reason opcionales) porque este mismo helper
    # cubre creaciones, cambios de estado y acciones sin datos anteriores o
    # posteriores (p.ej. un login); cada endpoint solo pasa lo que aplique.
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
