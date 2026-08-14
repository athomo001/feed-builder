"""Helper de router: envuelve un POST mutante con `Idempotency-Key` para que
un reintento con la misma clave y el mismo payload no duplique el efecto.
Distinto de `hub/idempotency.py` (valida solo la FORMA del header) y de
`hub/api/idempotency_store.py` (guarda/compara reintentos); este modulo
conecta ambos para los routers.

Autor: Athan Espinoza
"""
from typing import Callable, Optional

from hub.api.deps import APIState
from hub.api.errors import APIError
from hub.api.idempotency_store import IdempotencyConflict, get_cached, hash_request, store
from hub.idempotency import is_valid_idempotency_key


def with_idempotency(
    state: APIState,
    *,
    key: Optional[str],
    endpoint: str,
    payload: dict,
    compute: Callable[[], tuple],
) -> tuple:
    # El header es opcional: si el caller no lo manda, no hay nada que
    # deduplicar y `compute()` corre sin pasar por el store.
    if key is None:
        return compute()

    if not is_valid_idempotency_key(key):
        raise APIError(400, "Bad Request", "Idempotency-Key invalido", error_code="invalid_idempotency_key")

    # El hash del payload es lo que distingue "mismo request, reintentado"
    # de "misma clave reusada para otra cosa" -- el segundo caso es un error
    # del caller (IdempotencyConflict -> 409), no algo que debamos cachear.
    request_hash = hash_request(payload)
    try:
        cached = get_cached(state.idempotency_conn, key, endpoint, request_hash)
    except IdempotencyConflict as e:
        raise APIError(409, "Conflict", str(e), error_code="idempotency_key_conflict")

    # Ya existe una respuesta para esta (key, endpoint, payload): se devuelve
    # tal cual en vez de re-ejecutar `compute`, que es justamente el efecto
    # mutante que la Idempotency-Key existe para evitar duplicar.
    if cached is not None:
        return cached.status_code, cached.body

    status_code, body = compute()
    store(state.idempotency_conn, key=key, endpoint=endpoint, request_hash=request_hash, status_code=status_code, body=body)
    return status_code, body
