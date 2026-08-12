"""Contrato del header Idempotency-Key (spec/08-API-SECURITY.md "Escritura").

Basado en el draft IETF httpapi-idempotency-key-header citado en la spec:
un reintento con la misma clave y el mismo payload no debe duplicar el
efecto. Este modulo solo valida la FORMA del valor del header; el
almacenamiento/comparacion de reintentos es responsabilidad de la Entrega 2
(Admin API), no de este contrato.
"""

MAX_IDEMPOTENCY_KEY_LENGTH = 255


def is_valid_idempotency_key(key: str) -> bool:
    if not key or len(key) > MAX_IDEMPOTENCY_KEY_LENGTH:
        return False
    if not key.isascii():
        return False
    # Valores de header HTTP: caracteres visibles y espacio, sin CTLs (RFC 9110 field-value).
    return all(0x20 <= ord(c) < 0x7F for c in key)
