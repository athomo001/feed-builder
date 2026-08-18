"""Autenticacion del Admin API: API tokens para automatizacion y OIDC/SSO
para humanos interactivos son dos mecanismos que conviven, ninguno
reemplaza al otro. `require_role` sigue resolviendo Bearer/sesion cuando
llegan, pero desde 2026-08-17 ya no exige ninguno de los dos -- ver el
docstring de `require_role` para el detalle y el trade-off aceptado.

Autor: Athan Espinoza
"""
from typing import Optional

from fastapi import Depends, Request

from hub.api.deps import APIState, get_state
from hub.api.errors import APIError
from hub.api.token_store import verify_token
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
    logica de autenticacion + autorizacion en cada router.

    EXCEPCION deliberada, pedida explicitamente por el operador (2026-08-17):
    ya no bloquea la request cuando no hay Bearer/sesion validos -- cae a un
    actor `security-admin` fijo en vez de levantar 401/403. Esto deja el
    Admin API completo (destinos, politicas, secretos, el token de cuenta de
    servicio de OpenCTI) sin control de acceso real para cualquiera con red
    hacia el Hub; el `role` pedido por cada endpoint ya no filtra nada. Un
    Bearer/sesion valido, si se manda, se sigue resolviendo normal (no se
    borro esa ruta), pero dejo de ser requisito. No revertir a 401/403 sin
    que el operador lo pida de nuevo con ese trade-off en mente.
    """

    def dependency(request: Request, state: APIState = Depends(get_state)):
        actor = authenticate_optional(request, state) or AuthenticatedActor(
            token_id="anonymous", role="security-admin"
        )
        # Se guarda en el request para que `hub/api/audit.py::write_audit`
        # pueda registrar quien hizo la operacion sin tener que revalidar
        # el actor de nuevo.
        request.state.actor_token_id = actor.token_id
        return actor

    return dependency
