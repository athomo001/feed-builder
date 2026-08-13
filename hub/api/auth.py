"""Autenticacion del Admin API (spec/08-API-SECURITY.md "Autenticacion":
API tokens; decision Entrega 2 #5). `require_role` es la unica puerta:
revalida el rol en el SERVIDOR en cada endpoint de escritura (spec/08
API5, "ocultar un boton en la UI no sustituye la validacion server-side").
"""
from fastapi import Depends, Header, Request

from hub.api.deps import get_state
from hub.api.errors import APIError
from hub.api.token_store import APIToken, role_satisfies, verify_token


def _extract_bearer(authorization: str = Header(default=None)) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise APIError(
            401, "Unauthorized", "falta o es invalido el header 'Authorization: Bearer <token>'",
            error_code="unauthorized",
        )
    return authorization[len("bearer "):].strip()


def require_role(role: str):
    def dependency(request: Request, token_plain: str = Depends(_extract_bearer)) -> APIToken:
        state = get_state(request)
        token = verify_token(state.tokens_conn, token_plain)
        if token is None:
            raise APIError(401, "Unauthorized", "token invalido, revocado o expirado", error_code="unauthorized")
        if not role_satisfies(token.role, role):
            raise APIError(403, "Forbidden", f"se requiere rol '{role}' o superior", error_code="forbidden")
        request.state.actor_token_id = token.token_id
        return token

    return dependency
