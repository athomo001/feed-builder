"""Configuracion de la conexion a OpenCTI (URL, TLS, stream_id, token),
editable en caliente desde la Admin UI -- reemplaza las variables de
entorno OPENCTI_* que antes exigian redeploy para cambiar (ver
`hub/opencti_settings_store.py`). Requiere rol `security-admin` para
escribir, igual que las credenciales de destinos/secretos: son datos
sensibles (un token de OpenCTI). El valor del token nunca se devuelve por
ningun endpoint ni queda en la auditoria (mismo criterio que
`hub/api/routers/secrets.py`).

Autor: Athan Espinoza
"""
from fastapi import APIRouter, Depends, Request

from hub.api.audit import write_audit
from hub.api.auth import require_role
from hub.api.deps import APIState, get_state
from hub.api.errors import APIError
from hub.api.schemas import OpenCTISettingsUpdate
from hub.graphql_client import PING_QUERY
from hub.opencti_settings_store import (
    TOKEN_SECRET_NAME,
    get_opencti_settings,
    resolve_opencti_connection,
    upsert_opencti_settings,
)
from hub.secrets_store import get_secret, put_secret

router = APIRouter(prefix="/admin/api/v1/opencti-settings")


def _require_cipher(state: APIState):
    # Mismo 409 (no 500) que hub/api/routers/secrets.py::_require_cipher:
    # falta de clave de cifrado es un estado de configuracion esperado en
    # un despliegue que todavia no la seteo, no un error del servidor.
    if state.secret_cipher is None:
        raise APIError(
            409, "Conflict",
            "no hay clave de cifrado configurada (SECRET_ENCRYPTION_KEY / SECRET_ENCRYPTION_KEY_FILE)",
            error_code="secret_manager_not_configured",
        )
    return state.secret_cipher


@router.get("")
def get(state: APIState = Depends(get_state), _token=Depends(require_role("viewer"))):
    settings = get_opencti_settings(state.opencti_settings_conn, state.config.source_id)
    has_token = bool(
        state.secret_cipher and get_secret(state.secrets_conn, TOKEN_SECRET_NAME, cipher=state.secret_cipher)
    )
    if settings is None:
        return {"url": None, "tls_verify": True, "ca_cert_path": None, "stream_id": None, "has_token": has_token}
    return {**settings.model_dump(exclude={"source_id"}, mode="json"), "has_token": has_token}


@router.put("")
def update(
    payload: OpenCTISettingsUpdate,
    request: Request,
    state: APIState = Depends(get_state),
    token=Depends(require_role("security-admin")),
):
    before = get_opencti_settings(state.opencti_settings_conn, state.config.source_id)
    if payload.token is not None:
        # Guardar un token requiere la clave de cifrado -- mismo 409 que
        # cualquier otro secreto (ver secrets.py::_require_cipher), no un
        # 500: es un estado de configuracion esperado, no un bug.
        cipher = _require_cipher(state)
        put_secret(state.secrets_conn, TOKEN_SECRET_NAME, payload.token, cipher=cipher)
    elif before is None:
        raise APIError(
            409, "Conflict", "hace falta un token la primera vez que se configura OpenCTI",
            error_code="opencti_token_required",
        )
    settings = upsert_opencti_settings(
        state.opencti_settings_conn, state.config.source_id,
        url=payload.url, tls_verify=payload.tls_verify, ca_cert_path=payload.ca_cert_path, stream_id=payload.stream_id,
    )
    # Se audita la config de conexion (URL, TLS, stream_id) pero nunca el
    # token, este si haya venido en el payload (mismo criterio que secrets.py).
    write_audit(
        request, state, actor=token, action="opencti_settings.update",
        resource_type="opencti_settings", resource_id=state.config.source_id,
        before=before.model_dump(exclude={"source_id"}, mode="json") if before else None,
        after=settings.model_dump(exclude={"source_id"}, mode="json"),
    )
    return {**settings.model_dump(exclude={"source_id"}, mode="json"), "has_token": True}


@router.post("/test")
def test(state: APIState = Depends(get_state), _token=Depends(require_role("security-admin"))):
    connection = resolve_opencti_connection(
        state.opencti_settings_conn, state.config.source_id,
        secrets_conn=state.secrets_conn, cipher=state.secret_cipher,
    )
    if connection is None:
        raise APIError(
            409, "Conflict", "OpenCTI no esta configurado todavia (falta URL o token)",
            error_code="opencti_not_configured",
        )
    try:
        connection.client.query(PING_QUERY)
    except Exception as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True}
