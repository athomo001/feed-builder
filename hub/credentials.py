"""Resolucion de `credential_ref` (spec/05-FORMATS-DESTINATIONS.md destino
de ejemplo: `"credential_ref": "secret://destinations/qradar-prod"`;
spec/08-API-SECURITY.md "Seguridad de destino": "Secret manager... nunca en
claro").

Entrega 2 no levanta un secret manager real (eso es Entrega 5, spec/09).
Como placeholder documentado, `credential_ref` con el esquema `env://NAME`
se resuelve contra las variables de entorno del PROCESO del Hub (nunca del
`.env` compartido de OpenCTI, ver spec/02/03) al momento de enviar, y nunca
se persiste ni se loguea el valor resuelto.
"""
import os
from typing import Optional


class CredentialResolutionError(RuntimeError):
    pass


def resolve_credential_ref(credential_ref: str, *, env: Optional[dict] = None) -> str:
    env = env if env is not None else os.environ

    if not credential_ref or not credential_ref.startswith("env://"):
        raise CredentialResolutionError(
            f"credential_ref '{credential_ref}' no usa el esquema soportado 'env://' "
            "(secret manager real queda para Entrega 5, spec/09)"
        )

    var_name = credential_ref[len("env://"):]
    value = env.get(var_name)
    if not value:
        raise CredentialResolutionError(f"variable de entorno '{var_name}' no esta definida")
    return value
