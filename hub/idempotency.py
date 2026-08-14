"""Contrato del header Idempotency-Key.

Basado en el draft IETF httpapi-idempotency-key-header: un reintento con la
misma clave y el mismo payload no debe duplicar el efecto. Este modulo solo
valida la FORMA del valor del header; el almacenamiento/comparacion de
reintentos contra ese valor es responsabilidad de quien procesa la
peticion, no de este contrato.

Autor: Athan Espinoza
"""

MAX_IDEMPOTENCY_KEY_LENGTH = 255


def is_valid_idempotency_key(key: str) -> bool:
    # Limite defensivo: evita aceptar un valor arbitrariamente largo como
    # clave, lo que podria usarse para inflar el almacenamiento de reintentos.
    if not key or len(key) > MAX_IDEMPOTENCY_KEY_LENGTH:
        return False
    if not key.isascii():
        return False
    # Valores de header HTTP: caracteres visibles y espacio, sin CTLs (RFC 9110 field-value).
    return all(0x20 <= ord(c) < 0x7F for c in key)
