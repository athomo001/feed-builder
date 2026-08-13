"""Almacenamiento real de `Idempotency-Key` (spec/08-API-SECURITY.md
"Escritura": "Toda escritura no naturalmente idempotente (POST) acepta el
header Idempotency-Key... un reintento con la misma clave y el mismo
payload no debe duplicar el efecto").

`hub/idempotency.py` (Entrega 0) valida solo la FORMA del header y deja
escrito explicitamente que el almacenamiento es trabajo de Entrega 2; este
modulo es esa pieza.
"""
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel


class IdempotencyConflict(RuntimeError):
    """Misma Idempotency-Key, payload distinto (spec/08): 409/422 para el caller."""


class CachedResponse(BaseModel):
    status_code: int
    body: dict


def init_db(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS idempotency_keys (
            idempotency_key TEXT NOT NULL,
            endpoint TEXT NOT NULL,
            request_hash TEXT NOT NULL,
            status_code INTEGER NOT NULL,
            response_body TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (idempotency_key, endpoint)
        )
        """
    )
    conn.commit()
    return conn


def hash_request(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def get_cached(conn: sqlite3.Connection, key: str, endpoint: str, request_hash: str) -> Optional[CachedResponse]:
    """Devuelve la respuesta cacheada si (key, endpoint) ya se vio con el
    MISMO payload. Levanta `IdempotencyConflict` si la key se reuso con un
    payload distinto. Devuelve `None` si la key es nueva (el caller debe
    proceder y llamar a `store`)."""
    row = conn.execute(
        "SELECT request_hash, status_code, response_body FROM idempotency_keys "
        "WHERE idempotency_key = ? AND endpoint = ?",
        (key, endpoint),
    ).fetchone()
    if row is None:
        return None
    stored_hash, status_code, response_body = row
    if stored_hash != request_hash:
        raise IdempotencyConflict(
            f"Idempotency-Key '{key}' ya se uso en '{endpoint}' con un payload distinto"
        )
    return CachedResponse(status_code=status_code, body=json.loads(response_body))


def store(
    conn: sqlite3.Connection,
    *,
    key: str,
    endpoint: str,
    request_hash: str,
    status_code: int,
    body: dict,
    now: Optional[datetime] = None,
) -> None:
    now = now or datetime.now(timezone.utc)
    conn.execute(
        "INSERT OR REPLACE INTO idempotency_keys "
        "(idempotency_key, endpoint, request_hash, status_code, response_body, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (key, endpoint, request_hash, status_code, json.dumps(body), now.isoformat()),
    )
    conn.commit()
