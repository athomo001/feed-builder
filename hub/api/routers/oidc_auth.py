"""Router del flujo de login OIDC/SSO. `GET /oidc/login` redirige al IdP
(Authorization Code + PKCE); `GET /oidc/callback` intercambia el codigo,
valida el ID token, mapea el rol (`hub/oidc_client.py`) y crea una sesion
(`hub/oidc_session_store.py`, cookie `hub_session`
HttpOnly+Secure+SameSite=Lax); `POST /logout` la revoca; `GET /whoami` la
usa la UI al arrancar para saber si ya hay una sesion activa (o un Bearer
valido) sin tener que guardar nada en memoria.

El flujo en curso (`state`+`code_verifier` de PKCE) viaja en una cookie
propia, efimera (10 min) y HttpOnly -- nunca en la sesion de la UI ni en
localStorage. Los API tokens existentes siguen sin cambios (`hub/api/
auth.py::require_role` acepta cualquiera de los dos mecanismos).

Autor: Athan Espinoza
"""
import base64
import json

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse

from hub.api.auth import authenticate_optional
from hub.api.deps import APIState, get_state
from hub.api.errors import APIError
from hub.credentials import resolve_credential_ref
from hub.oidc_client import (
    OIDCError,
    build_authorize_url,
    discover,
    exchange_code_for_tokens,
    fetch_jwks,
    generate_pkce_pair,
    generate_state,
    map_claims_to_role,
    validate_id_token,
)
from hub.oidc_session_store import create_session, revoke_session

router = APIRouter(prefix="/admin/api/v1/auth", tags=["auth"])

_FLOW_COOKIE = "hub_oidc_flow"
_SESSION_COOKIE = "hub_session"
_FLOW_COOKIE_MAX_AGE_SECONDS = 600


def _require_oidc_configured(state: APIState) -> None:
    # 409 (no 500): OIDC sin configurar es un estado esperado en despliegues
    # que solo usan API tokens, no un fallo del servidor.
    if not (state.config.oidc_issuer_url and state.config.oidc_client_id and state.config.oidc_redirect_uri):
        raise APIError(
            409, "Conflict",
            "OIDC no esta configurado (OIDC_ISSUER_URL / OIDC_CLIENT_ID / OIDC_REDIRECT_URI)",
            error_code="oidc_not_configured",
        )


@router.get("/oidc/login")
def login(state: APIState = Depends(get_state)):
    _require_oidc_configured(state)
    try:
        discovery = discover(state.config.oidc_issuer_url)
    except OIDCError as e:
        raise APIError(502, "Bad Gateway", f"no se pudo contactar al IdP: {e}", error_code="oidc_idp_unavailable")

    # `oidc_state` protege el callback contra CSRF (se compara contra el
    # query param `state` que vuelva del IdP); `code_verifier` es el secreto
    # de PKCE que solo este navegador conoce hasta el intercambio final.
    oidc_state = generate_state()
    code_verifier, code_challenge = generate_pkce_pair()
    authorize_url = build_authorize_url(
        discovery,
        client_id=state.config.oidc_client_id,
        redirect_uri=state.config.oidc_redirect_uri,
        state=oidc_state,
        code_challenge=code_challenge,
    )

    response = RedirectResponse(authorize_url, status_code=302)
    # El flujo completo (state + code_verifier) viaja en su propia cookie en
    # vez de guardarse en memoria del proceso: evita depender de estado de
    # servidor entre la redireccion y el callback (relevante con multiples
    # workers) y no toca la sesion de la UI ni localStorage.
    flow_payload = json.dumps({"state": oidc_state, "code_verifier": code_verifier}).encode("utf-8")
    response.set_cookie(
        _FLOW_COOKIE,
        # base64url SIN padding ('=' fuerza el quoting de http.cookies, RFC
        # 2616 quoted-string, que algunos clientes HTTP no des-escapan de
        # forma transparente al releerlo) -- se re-agrega el padding al
        # decodificar en el callback.
        base64.urlsafe_b64encode(flow_payload).decode("ascii").rstrip("="),
        max_age=_FLOW_COOKIE_MAX_AGE_SECONDS,
        httponly=True,
        secure=True,
        samesite="lax",
    )
    return response


def _b64url_decode(value: str) -> bytes:
    # Inverso del encode en `login`: ahi se quito el padding '=' antes de
    # guardarlo en la cookie, aca se recalcula y se vuelve a agregar (el
    # padding siempre completa a un multiplo de 4 caracteres).
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


@router.get("/oidc/callback")
def callback(
    request: Request,
    code: str = Query(...),
    state_param: str = Query(..., alias="state"),
    state: APIState = Depends(get_state),
):
    _require_oidc_configured(state)
    flow_cookie = request.cookies.get(_FLOW_COOKIE)
    if not flow_cookie:
        raise APIError(
            400, "Bad Request", "flujo OIDC expirado o invalido, reintenta el login", error_code="oidc_flow_missing"
        )
    try:
        flow = json.loads(_b64url_decode(flow_cookie))
    except (ValueError, UnicodeDecodeError):
        raise APIError(400, "Bad Request", "cookie de flujo OIDC invalida", error_code="oidc_flow_missing")
    # Comparacion del 'state' guardado en la cookie del propio navegador
    # contra el que llego en la URL del callback: si un atacante engancha a
    # la victima a un callback ajeno (o reusa uno viejo), estos valores no
    # coincidiran.
    if flow.get("state") != state_param:
        raise APIError(400, "Bad Request", "state invalido (posible CSRF)", error_code="oidc_state_mismatch")

    try:
        discovery = discover(state.config.oidc_issuer_url)
        # El client_secret solo se resuelve si esta configurado: clientes
        # confidenciales (backend-to-backend) lo usan ademas de PKCE,
        # clientes publicos dependen solo del code_verifier.
        client_secret = (
            resolve_credential_ref(
                state.config.oidc_client_secret_ref, secrets_conn=state.secrets_conn, cipher=state.secret_cipher
            )
            if state.config.oidc_client_secret_ref
            else None
        )
        tokens = exchange_code_for_tokens(
            discovery,
            client_id=state.config.oidc_client_id,
            client_secret=client_secret,
            redirect_uri=state.config.oidc_redirect_uri,
            code=code,
            code_verifier=flow["code_verifier"],
        )
        jwks = fetch_jwks(discovery)
        claims = validate_id_token(
            tokens["id_token"], jwks=jwks, issuer=discovery["issuer"], audience=state.config.oidc_client_id
        )
    except OIDCError as e:
        # Cualquier fallo del intercambio o de la validacion del ID token se
        # colapsa en un 401 generico: no conviene distinguirle al cliente si
        # fallo el intercambio de codigo, el JWKS o la firma, ya que en
        # todos los casos la accion correcta es la misma (reintentar login).
        raise APIError(401, "Unauthorized", f"login OIDC fallo: {e}", error_code="oidc_login_failed")

    role = map_claims_to_role(
        claims, role_claim=state.config.oidc_role_claim, role_mapping=state.config.oidc_role_mapping
    )
    _, plaintext = create_session(
        state.oidc_sessions_conn, subject=claims["sub"], role=role, ttl_seconds=state.config.oidc_session_ttl_seconds
    )

    # Redirige al primer origen configurado de la UI, o a la raiz relativa
    # si no hay ninguno configurado -- evita que el login quede "colgado"
    # en un despliegue donde ADMIN_UI_ORIGINS no se seteo todavia.
    redirect_target = state.config.admin_ui_origins[0] if state.config.admin_ui_origins else "/"
    response = RedirectResponse(redirect_target, status_code=302)
    response.delete_cookie(_FLOW_COOKIE)
    response.set_cookie(
        _SESSION_COOKIE,
        plaintext,
        max_age=state.config.oidc_session_ttl_seconds,
        httponly=True,
        secure=True,
        samesite="lax",
    )
    return response


@router.post("/logout")
def logout(request: Request, state: APIState = Depends(get_state)):
    session_cookie = request.cookies.get(_SESSION_COOKIE)
    if session_cookie:
        revoke_session(state.oidc_sessions_conn, session_cookie)
    response = JSONResponse({"logged_out": True})
    response.delete_cookie(_SESSION_COOKIE)
    return response


@router.get("/whoami")
def whoami(request: Request, state: APIState = Depends(get_state)):
    actor = authenticate_optional(request, state)
    if actor is None:
        raise APIError(401, "Unauthorized", "sin sesion activa", error_code="unauthorized")
    return {"token_id": actor.token_id, "role": actor.role}
