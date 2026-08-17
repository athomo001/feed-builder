"""Gestion de API tokens desde el Admin API/UI (rol `security-admin`).
El PRIMER token del despliegue sigue sin poder crearse por HTTP (huevo-y-
gallina de auth, ver README.md 14.2, script contra tokens.sqlite3) -- estos
endpoints son para crear/revocar los SIGUIENTES una vez que ya hay al
menos uno. El valor en claro se devuelve UNA sola vez al crear, igual que
un secreto (hub/api/routers/secrets.py); listar nunca lo expone.

Autor: Athan Espinoza
"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Request

from hub.api.audit import write_audit
from hub.api.auth import require_role
from hub.api.deps import APIState, get_state
from hub.api.errors import APIError
from hub.api.schemas import TokenCreate
from hub.api.token_store import create_token, get_token, list_tokens, revoke

router = APIRouter(prefix="/admin/api/v1/tokens")


def _public(token) -> dict:
    # token_hash nunca se serializa: mismo criterio que secrets.py con el
    # valor cifrado, aunque aca sea un hash de un solo sentido (igual no
    # hay motivo para exponerlo).
    return {
        "token_id": token.token_id,
        "role": token.role,
        "created_at": token.created_at,
        "expires_at": token.expires_at,
        "revoked": token.revoked,
    }


@router.get("")
def list_all(state: APIState = Depends(get_state), _token=Depends(require_role("security-admin"))):
    return [_public(t) for t in list_tokens(state.tokens_conn)]


@router.post("", status_code=201)
def create(
    payload: TokenCreate,
    request: Request,
    state: APIState = Depends(get_state),
    token=Depends(require_role("security-admin")),
):
    expires_at = (
        datetime.now(timezone.utc) + timedelta(days=payload.expires_in_days)
        if payload.expires_in_days is not None
        else None
    )
    created, plaintext = create_token(state.tokens_conn, role=payload.role, expires_at=expires_at)
    # Se audita rol/expiracion, nunca el valor en claro (mismo criterio que
    # secret.put en hub/api/routers/secrets.py).
    write_audit(
        request, state, actor=token, action="token.create",
        resource_type="token", resource_id=created.token_id,
        after={"role": created.role, "expires_at": created.expires_at.isoformat() if created.expires_at else None},
    )
    return {**_public(created), "plaintext": plaintext}


@router.post("/{token_id}/revoke")
def revoke_token(
    token_id: str, request: Request, state: APIState = Depends(get_state), token=Depends(require_role("security-admin"))
):
    existing = get_token(state.tokens_conn, token_id)
    if existing is None:
        raise APIError(404, "Not Found", f"token '{token_id}' no existe", error_code="token_not_found")
    revoke(state.tokens_conn, token_id)
    write_audit(
        request, state, actor=token, action="token.revoke",
        resource_type="token", resource_id=token_id,
        before={"revoked": existing.revoked}, after={"revoked": True},
    )
    return {"token_id": token_id, "revoked": True}
