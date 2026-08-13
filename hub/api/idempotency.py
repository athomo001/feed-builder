"""Helper de router: envuelve un POST mutante con `Idempotency-Key`
(spec/08 "Escritura"). Distinto de `hub/idempotency.py` (valida solo la
FORMA del header) y de `hub/api/idempotency_store.py` (guarda/compara
reintentos); este modulo conecta ambos para los routers.
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
    if key is None:
        return compute()

    if not is_valid_idempotency_key(key):
        raise APIError(400, "Bad Request", "Idempotency-Key invalido", error_code="invalid_idempotency_key")

    request_hash = hash_request(payload)
    try:
        cached = get_cached(state.idempotency_conn, key, endpoint, request_hash)
    except IdempotencyConflict as e:
        raise APIError(409, "Conflict", str(e), error_code="idempotency_key_conflict")

    if cached is not None:
        return cached.status_code, cached.body

    status_code, body = compute()
    store(state.idempotency_conn, key=key, endpoint=endpoint, request_hash=request_hash, status_code=status_code, body=body)
    return status_code, body
