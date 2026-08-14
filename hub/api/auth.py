"""Autenticacion del Admin API: API tokens para automatizacion y OIDC/SSO
para humanos interactivos son dos mecanismos que conviven, ninguno
reemplaza al otro. `require_role` es la unica puerta: revalida el rol en
el SERVIDOR en cada endpoint de escritura -- ocultar un boton en la UI no
sustituye esa validacion server-side -- sea cual sea el mecanismo usado.

Autor: Athan Espinoza
"""
from typing import Optional

from fastapi import Depends, Request

from hub.api.deps import APIState, get_state
from hub.api.errors import APIError
from hub.api.token_store import role_satisfies, verify_token
from hub.oidc_session_store import verify_session

_SESSION_COOKIE = "hub_session"


class AuthenticatedActor:
    """Envoltorio minimo para que una sesion OIDC exponga las mismas dos
    propiedades que `hub/api/audit.py::write_audit` ya lee de un `APIToken`
    (`.token_id`, `.role`) sin tener que tocar ese modulo."""

    def __init__(self, *, token_id: str, role: str):
        self.token_id = token_id
        self.role = role


def authenticate_optional(request: Request, state: Optional[APIState] = None):
    """Prueba Bearer primero; si no hay o no es valido, prueba la cookie de
    sesion OIDC. Devuelve `None` en vez de levantar cuando ninguno de los
    dos aplica, para que endpoints como `GET /auth/whoami` puedan devolver
    un 401 explicito con su propio mensaje en vez de heredar una excepcion
    generica de este helper."""
    state = state or get_state(request)

    # Bearer se intenta primero porque es el mecanismo pensado para
    # automatizacion (scripts, integraciones) sin cookies de navegador; si
    # el header esta ausente o el token no es valido, se cae al flujo de
    # sesion sin fallar de inmediato.
    authorization = request.headers.get("authorization") or ""
    if authorization.lower().startswith("bearer "):
        token = verify_token(state.tokens_conn, authorization[len("bearer "):].strip())
        if token is not None:
            return token

    # `state.oidc_sessions_conn` puede ser None cuando OIDC no esta
    # configurado en este despliegue -- en ese caso ni se intenta leer la
    # cookie, para no depender de una conexion inexistente.
    session_cookie = request.cookies.get(_SESSION_COOKIE)
    if session_cookie and state.oidc_sessions_conn is not None:
        session = verify_session(state.oidc_sessions_conn, session_cookie)
        if session is not None:
            return AuthenticatedActor(token_id=f"oidc:{session.session_id}", role=session.role)

    return None


def require_role(role: str):
    """Fabrica una dependencia de FastAPI en vez de una funcion fija porque
    cada endpoint necesita exigir un rol minimo distinto (`viewer`,
    `security-admin`, etc.); parametrizar por `role` evita duplicar esta
    logica de autenticacion + autorizacion en cada router."""

    def dependency(request: Request, state: APIState = Depends(get_state)):
        actor = authenticate_optional(request, state)
        if actor is None:
            raise APIError(
                401, "Unauthorized", "falta un token Bearer valido o una sesion activa", error_code="unauthorized"
            )
        if not role_satisfies(actor.role, role):
            raise APIError(403, "Forbidden", f"se requiere rol '{role}' o superior", error_code="forbidden")
        # Se guarda en el request para que `hub/api/audit.py::write_audit`
        # pueda registrar quien hizo la operacion sin tener que revalidar
        # el actor de nuevo.
        request.state.actor_token_id = actor.token_id
        return actor

    return dependency
