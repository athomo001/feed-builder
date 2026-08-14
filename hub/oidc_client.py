"""Cliente OIDC generico, Authorization Code + PKCE.

No hay una instancia real de un IdP (Keycloak/Okta/Azure AD) disponible en
este entorno -- mismo patron de honestidad que `hub/adapters/qradar_adapter.py`
para QRadar: la logica de descubrimiento/intercambio/validacion sigue el
estandar OIDC/OAuth2 documentado publicamente, probada contra un IdP
simulado en los tests (JWKS + ID token firmados con un keypair RSA propio),
no contra un proveedor real.

Validacion de ID token deliberadamente estricta: algoritmos permitidos
explicitos (nunca `alg=none` ni HS256 con un secreto adivinable), `iss`/
`aud`/`exp` siempre verificados, tolerancia de reloj acotada.

Autor: Athan Espinoza
"""
import base64
import hashlib
import secrets
from typing import Optional
from urllib.parse import urlencode

import jwt
import requests
from jwt.algorithms import RSAAlgorithm

# Lista blanca de algoritmos aceptados: evita que un token manipulado fuerce
# `alg=none` (sin firma, bypass total) o HS256 usando la clave publica RSA
# como si fuera un secreto simetrico (confusion de algoritmos) -- ambos son
# ataques conocidos contra bibliotecas JWT permisivas.
_ALLOWED_ALGORITHMS = ["RS256"]
# Tolerancia de reloj acotada a proposito: absorbe pequenos desfaces entre
# este proceso y el IdP sin abrir una ventana amplia donde un token ya
# expirado siga siendo aceptado.
_CLOCK_SKEW_LEEWAY_SECONDS = 60


class OIDCError(RuntimeError):
    """Excepcion propia para que los llamadores (los routers) puedan
    distinguir un fallo del flujo OIDC de cualquier otro error y mapearlo a
    un codigo HTTP especifico (502 si el IdP no responde, 401 si el token o
    el intercambio son invalidos)."""

    pass


def discover(issuer_url: str, *, session=None) -> dict:
    # `session` es inyectable para poder simular la respuesta del IdP en los
    # tests sin hacer llamadas de red reales.
    session = session or requests
    resp = session.get(f"{issuer_url.rstrip('/')}/.well-known/openid-configuration", timeout=10)
    if resp.status_code >= 400:
        raise OIDCError(f"no se pudo descubrir el IdP en '{issuer_url}': HTTP {resp.status_code}")
    return resp.json()


def fetch_jwks(discovery: dict, *, session=None) -> dict:
    # Falla temprano y con un mensaje explicito si el JWKS no esta disponible:
    # sin estas claves publicas no hay forma de validar la firma del ID
    # token, mejor eso que dejar que un JSON incompleto rompa mas adelante.
    session = session or requests
    resp = session.get(discovery["jwks_uri"], timeout=10)
    if resp.status_code >= 400:
        raise OIDCError(f"no se pudo obtener el JWKS: HTTP {resp.status_code}")
    return resp.json()


def generate_pkce_pair() -> tuple[str, str]:
    """(code_verifier, code_challenge) -- S256, RFC 7636.

    PKCE existe para proteger clientes publicos (esta UI, sin client_secret):
    sin el, un atacante que intercepte el `code` de la redireccion podria
    canjearlo el mismo por tokens. El `code_challenge` (hash del verifier) va
    en la URL de autorizacion; solo quien tiene el `code_verifier` original
    puede completar el intercambio.
    """
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii").rstrip("=")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


def generate_state() -> str:
    # Valor aleatorio impredecible que el callback debe devolver identico:
    # protege el login contra CSRF, ya que un atacante no puede forjar una
    # redireccion de callback valida sin conocer este valor de antemano.
    return secrets.token_urlsafe(24)


def build_authorize_url(
    discovery: dict, *, client_id: str, redirect_uri: str, state: str, code_challenge: str, scope: str = "openid profile"
) -> str:
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": scope,
        "state": state,
        "code_challenge": code_challenge,
        # Fijo en "S256": el metodo "plain" de PKCE manda el verifier tal
        # cual en esta URL (que puede quedar en logs/historial), anulando la
        # proteccion que PKCE deberia dar.
        "code_challenge_method": "S256",
    }
    return f"{discovery['authorization_endpoint']}?{urlencode(params)}"


def exchange_code_for_tokens(
    discovery: dict,
    *,
    client_id: str,
    client_secret: Optional[str],
    redirect_uri: str,
    code: str,
    code_verifier: str,
    session=None,
) -> dict:
    session = session or requests
    data = {
        "grant_type": "authorization_code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code": code,
        "code_verifier": code_verifier,
    }
    # client_secret es opcional: los clientes publicos (esta UI, una SPA) no
    # pueden guardar un secreto de forma segura, asi que en ese caso es
    # `code_verifier` (PKCE) lo que autentica el intercambio ante el IdP.
    if client_secret:
        data["client_secret"] = client_secret
    resp = session.post(discovery["token_endpoint"], data=data, timeout=10)
    if resp.status_code >= 400:
        raise OIDCError(f"el IdP rechazo el intercambio de codigo: HTTP {resp.status_code}")
    return resp.json()


def validate_id_token(id_token: str, *, jwks: dict, issuer: str, audience: str) -> dict:
    try:
        header = jwt.get_unverified_header(id_token)
    except jwt.InvalidTokenError as e:
        raise OIDCError(f"ID token malformado: {e}") from e

    kid = header.get("kid")
    # Selecciona la clave publica por 'kid' en vez de probar todas las del
    # JWKS: valida contra la clave que el propio token dice usar, sin
    # ambiguedad, y falla explicito si hubo una rotacion de claves en el IdP
    # y este proceso todavia no descargo el JWKS actualizado.
    key_data = next((k for k in jwks.get("keys", []) if k.get("kid") == kid), None)
    if key_data is None:
        raise OIDCError("no se encontro una clave JWKS que coincida con el 'kid' del ID token")

    public_key = RSAAlgorithm.from_jwk(key_data)
    try:
        claims = jwt.decode(
            id_token,
            key=public_key,
            algorithms=_ALLOWED_ALGORITHMS,
            issuer=issuer,
            audience=audience,
            leeway=_CLOCK_SKEW_LEEWAY_SECONDS,
            # Se exige que estos claims esten presentes (no solo que sean
            # validos si aparecen): un IdP mal configurado que omita 'exp'
            # emitiria tokens que nunca expiran.
            options={"require": ["exp", "iat", "sub"]},
        )
    except jwt.InvalidTokenError as e:
        raise OIDCError(f"ID token invalido: {e}") from e
    return claims


def map_claims_to_role(claims: dict, *, role_claim: str, role_mapping: dict, default_role: str = "viewer") -> str:
    """Un claim puede ser un string suelto o una lista (grupos/roles). La
    primera entrada que matchee `role_mapping` gana; sin match, `default_role`
    (nunca falla por un mapeo incompleto -- el operador ve el rol resultante
    y puede ajustar `OIDC_ROLE_MAPPING`)."""
    raw = claims.get(role_claim)
    values = raw if isinstance(raw, list) else [raw] if raw else []
    for value in values:
        if value in role_mapping:
            return role_mapping[value]
    return default_role
