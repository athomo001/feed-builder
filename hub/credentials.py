"""Resolucion de `credential_ref` (ejemplo:
`"credential_ref": "secret://destinations/qradar-prod"`): la credencial de
un destino nunca se guarda en claro en el config del destino, solo una
referencia que se resuelve en el momento de usarla.

Dos esquemas soportados:
- `env://NAME`: variables de entorno del PROCESO del Hub (nunca del `.env`
  compartido de OpenCTI -- son procesos distintos con su propio entorno,
  mezclarlos seria una fuente de confusion y de fugas entre componentes).
- `secret://NAME`: secretos cifrados en reposo (`hub/secrets_store.py`,
  con clave externa -- ver `hub/secret_encryption.py` para el porque de esa
  decision). Requiere que quien llama pase `secrets_conn`/`cipher`; sin
  ellos configurados, falla explicito en vez de tratar el `secret://` como
  si no existiera (silenciar el error dejaria un destino "funcionando" sin
  credencial real, entregando sin autenticacion o fallando de forma
  confusa mas adelante).

En ningun caso se persiste ni se loguea el valor resuelto.

Autor: Athan Espinoza
"""
import os
from typing import Optional

from hub.secret_encryption import SecretCipher


class CredentialResolutionError(RuntimeError):
    pass


def resolve_credential_ref(
    credential_ref: str,
    *,
    env: Optional[dict] = None,
    secrets_conn=None,
    cipher: Optional[SecretCipher] = None,
) -> str:
    if not credential_ref:
        raise CredentialResolutionError("credential_ref vacio")

    if credential_ref.startswith("env://"):
        # `env` inyectable (no siempre os.environ): permite testear esta
        # funcion sin tocar el entorno real del proceso.
        env = env if env is not None else os.environ
        var_name = credential_ref[len("env://"):]
        value = env.get(var_name)
        # `not value` (no `value is None`) trata una variable vacia igual
        # que una no definida: una credencial vacia nunca es un valor
        # valido, asi que es mejor fallar explicito que intentar
        # autenticar con un string vacio.
        if not value:
            raise CredentialResolutionError(f"variable de entorno '{var_name}' no esta definida")
        return value

    if credential_ref.startswith("secret://"):
        # Import tardio (dentro de la funcion, no al tope del archivo): un
        # caller que solo usa `env://` (el caso mas comun, sin secret
        # manager configurado) no necesita pagar el costo de importar
        # hub.secrets_store (y su dependencia de sqlite3) si nunca entra a
        # esta rama.
        from hub.secrets_store import get_secret

        name = credential_ref[len("secret://"):]
        if cipher is None or secrets_conn is None:
            raise CredentialResolutionError(
                "credential_ref usa 'secret://' pero no hay secret manager configurado "
                "(SECRET_ENCRYPTION_KEY / SECRET_ENCRYPTION_KEY_FILE)"
            )
        value = get_secret(secrets_conn, name, cipher=cipher)
        if value is None:
            raise CredentialResolutionError(f"secreto '{name}' no existe")
        return value

    raise CredentialResolutionError(
        f"credential_ref '{credential_ref}' no usa un esquema soportado ('env://' o 'secret://')"
    )
