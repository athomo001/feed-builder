"""Cifrado en reposo con clave externa: se eligio este enfoque en vez de
integrar un secret manager externo real porque cumple el mismo objetivo de
seguridad (el secreto nunca queda en texto plano en el almacenamiento del
Hub) sin depender de infraestructura adicional que el operador tendria que
desplegar y mantener.

La clave NUNCA vive en la base de datos ni en `state_dir` -- mismo
principio que `hub/credentials.py` ya aplica al esquema `env://` (nunca se
persiste el secreto resuelto). Fernet (`cryptography`, AES-128-CBC +
HMAC-SHA256 autenticado): descifrar con una clave incorrecta o un dato
corrupto falla explícito (`InvalidToken`), nunca en silencio -- preferible
a devolver basura descifrada sin que nadie lo note.

Autor: Athan Espinoza
"""
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken


class SecretCipherError(RuntimeError):
    pass


class SecretCipher:
    def __init__(self, key: bytes):
        self._fernet = Fernet(key)

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def decrypt(self, ciphertext: str) -> str:
        try:
            return self._fernet.decrypt(ciphertext.encode("ascii")).decode("utf-8")
        except InvalidToken as e:
            # Se relanza como un error propio del dominio (no InvalidToken
            # de la libreria) para que quien llama no necesite conocer ni
            # importar la excepcion interna de `cryptography`.
            raise SecretCipherError("no se pudo descifrar: clave incorrecta o dato corrupto") from e

    @staticmethod
    def generate_key() -> str:
        """Clave lista para `SECRET_ENCRYPTION_KEY` -- setup inicial o rotación."""
        return Fernet.generate_key().decode("ascii")


def load_cipher(config) -> Optional[SecretCipher]:
    """`None` si no hay clave configurada. Resolver un `secret://` sin clave
    configurada falla explícito en `hub/credentials.py::resolve_credential_ref`,
    no acá -- instalaciones que nunca usan `secret://` no necesitan la clave."""
    key = _load_key_material(config)
    if key is None:
        return None
    return SecretCipher(key)


# Dos formas de configurar la misma clave: variable de entorno (simple,
# suficiente para la mayoria de despliegues) o archivo (para operadores que
# ya distribuyen secretos como archivos montados, por ejemplo un Docker/K8s
# secret) -- la variable de entorno gana si ambas estan presentes.
def _load_key_material(config) -> Optional[bytes]:
    if config.secret_encryption_key:
        return config.secret_encryption_key.encode("ascii")
    if config.secret_encryption_key_file:
        with open(config.secret_encryption_key_file, "r", encoding="ascii") as f:
            return f.read().strip().encode("ascii")
    return None
