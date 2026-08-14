"""Endpoints de gestion de secretos con cifrado en reposo
(`hub/secret_encryption.py`, `hub/secrets_store.py`) -- requieren rol
`security-admin`, igual que las credenciales de destino. El valor en claro
nunca se devuelve por ningun endpoint ni queda en la auditoria (solo se
audita el nombre).

`POST /rotate-key` re-cifra todos los secretos con una clave nueva y
actualiza el cifrador en memoria del proceso para que las llamadas
siguientes ya usen la clave nueva -- pero el operador igual debe
actualizar `SECRET_ENCRYPTION_KEY`/`_FILE` antes del proximo reinicio del
proceso, o los secretos vuelven a ser ilegibles con la clave vieja que
quedo en el entorno.

Autor: Athan Espinoza
"""
from fastapi import APIRouter, Depends, Request

from hub.api.audit import write_audit
from hub.api.auth import require_role
from hub.api.deps import APIState, get_state
from hub.api.errors import APIError
from hub.api.schemas import RotateKeyRequest, SecretCreate
from hub.secret_encryption import SecretCipher, SecretCipherError
from hub.secrets_store import delete_secret, get_secret, list_secret_names, put_secret, rotate_key

router = APIRouter(prefix="/admin/api/v1/secrets")


def _require_cipher(state: APIState) -> SecretCipher:
    # 409 (no 500): falta de clave de cifrado es un estado de configuracion
    # esperado en un despliegue que todavia no habilito el secret manager,
    # no un error del servidor.
    if state.secret_cipher is None:
        raise APIError(
            409, "Conflict",
            "no hay clave de cifrado configurada (SECRET_ENCRYPTION_KEY / SECRET_ENCRYPTION_KEY_FILE)",
            error_code="secret_manager_not_configured",
        )
    return state.secret_cipher


@router.get("")
def list_all(state: APIState = Depends(get_state), _token=Depends(require_role("viewer"))):
    # Solo nombres, nunca valores: listar no requiere el cifrador ni el rol
    # elevado que si exigen los endpoints que leen o escriben el secreto.
    return {"names": list_secret_names(state.secrets_conn)}


@router.post("", status_code=201)
def create_or_update(
    payload: SecretCreate,
    request: Request,
    state: APIState = Depends(get_state),
    token=Depends(require_role("security-admin")),
):
    cipher = _require_cipher(state)
    put_secret(state.secrets_conn, payload.name, payload.value, cipher=cipher)
    # Se audita el nombre del secreto, nunca su valor (ver docstring del
    # modulo): el registro de auditoria debe ser seguro de mostrar sin
    # exponer el material sensible que protege.
    write_audit(
        request, state, actor=token, action="secret.put",
        resource_type="secret", resource_id=payload.name,
    )
    return {"name": payload.name}


@router.delete("/{name}")
def delete(
    name: str, request: Request, state: APIState = Depends(get_state), token=Depends(require_role("security-admin"))
):
    existed = delete_secret(state.secrets_conn, name)
    if not existed:
        raise APIError(404, "Not Found", f"secreto '{name}' no existe", error_code="secret_not_found")
    write_audit(request, state, actor=token, action="secret.delete", resource_type="secret", resource_id=name)
    return {"name": name, "deleted": True}


@router.post("/{name}/test")
def test(name: str, state: APIState = Depends(get_state), _token=Depends(require_role("security-admin"))):
    cipher = _require_cipher(state)
    try:
        value = get_secret(state.secrets_conn, name, cipher=cipher)
    # Un fallo de descifrado (ej. la clave configurada ya no es la que se
    # uso para cifrar este secreto) se reporta como `ok: False` en vez de
    # propagar un 500: distingue "el secreto no existe" (404) de "existe
    # pero no se puede leer con la clave actual" (ok: false), que requieren
    # acciones distintas del operador.
    except SecretCipherError as e:
        return {"name": name, "ok": False, "error": str(e)}
    if value is None:
        raise APIError(404, "Not Found", f"secreto '{name}' no existe", error_code="secret_not_found")
    return {"name": name, "ok": True}


@router.post("/rotate-key")
def rotate(
    payload: RotateKeyRequest,
    request: Request,
    state: APIState = Depends(get_state),
    token=Depends(require_role("security-admin")),
):
    old_cipher = _require_cipher(state)
    new_cipher = SecretCipher(payload.new_key.encode("ascii"))
    # Re-cifra todos los secretos existentes con la clave nueva antes de
    # adoptarla: si `rotate_key` fallara a mitad de camino, `state.secret_cipher`
    # todavia apuntaria a la clave vieja consistente con lo que hay guardado.
    rotated = rotate_key(state.secrets_conn, old_cipher=old_cipher, new_cipher=new_cipher)
    # Se actualiza el cifrador en memoria para que las llamadas siguientes en
    # este mismo proceso ya usen la clave nueva -- ver docstring del modulo
    # para la limitacion de esto (variable de entorno del proceso sin tocar).
    state.secret_cipher = new_cipher
    write_audit(
        request, state, actor=token, action="secret.rotate_key",
        resource_type="secret", resource_id="*", after={"rotated_count": rotated},
    )
    return {"rotated": rotated}
