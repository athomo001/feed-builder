"""API tokens locales (spec/08-API-SECURITY.md "Autenticacion": "API tokens
separados para automatizacion, con hash en base de datos, expiracion,
scopes y rotacion"). Decision de la Entrega 2 (spec/09 decision #5): sin
OIDC/SSO todavia, sin usuario/contrasena local -- eso queda para cuando
exista la consola Angular real (Entrega 3) o un proveedor corporativo
confirmado.

El token en claro se genera con `secrets.token_urlsafe` (alta entropia) y
se devuelve UNA sola vez al crearlo; solo su hash SHA-256 se persiste. A
diferencia de una contrasena elegida por un humano, un token de 256 bits de
entropia no necesita Argon2id (ese costo es para resistir fuerza bruta
offline sobre secretos de baja entropia; aqui el hash solo protege contra
una fuga de la base, no contra adivinar el token).
"""
import hashlib
import json
import secrets
import sqlite3
from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field

Role = Literal["viewer", "operator", "policy-admin", "security-admin"]

# spec/08 "Roles locales": cada rol de mayor privilegio cubre las acciones
# del anterior (jerarquia simple, no matriz de permisos independiente).
_ROLE_RANK = {"viewer": 0, "operator": 1, "policy-admin": 2, "security-admin": 3}


def role_satisfies(actual: str, required: str) -> bool:
    return _ROLE_RANK.get(actual, -1) >= _ROLE_RANK.get(required, 99)


class APIToken(BaseModel):
    token_id: str
    token_hash: str
    role: Role
    scopes: list[str] = Field(default_factory=list)
    created_at: datetime
    expires_at: Optional[datetime] = None
    revoked: bool = False


def _hash_token(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def init_db(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS api_tokens (
            token_id TEXT PRIMARY KEY,
            token_hash TEXT NOT NULL UNIQUE,
            role TEXT NOT NULL,
            scopes TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT,
            revoked INTEGER NOT NULL
        )
        """
    )
    conn.commit()
    return conn


def create_token(
    conn: sqlite3.Connection,
    *,
    role: Role,
    scopes: Optional[list[str]] = None,
    expires_at: Optional[datetime] = None,
    now: Optional[datetime] = None,
) -> tuple[APIToken, str]:
    now = now or datetime.now(timezone.utc)
    plaintext = secrets.token_urlsafe(32)
    token = APIToken(
        token_id=secrets.token_hex(8),
        token_hash=_hash_token(plaintext),
        role=role,
        scopes=scopes or [],
        created_at=now,
        expires_at=expires_at,
        revoked=False,
    )
    conn.execute(
        "INSERT INTO api_tokens (token_id, token_hash, role, scopes, created_at, expires_at, revoked) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            token.token_id,
            token.token_hash,
            token.role,
            json.dumps(token.scopes),
            token.created_at.isoformat(),
            token.expires_at.isoformat() if token.expires_at else None,
            int(token.revoked),
        ),
    )
    conn.commit()
    return token, plaintext


def _row_to_token(row) -> APIToken:
    return APIToken(
        token_id=row[0],
        token_hash=row[1],
        role=row[2],
        scopes=json.loads(row[3]),
        created_at=row[4],
        expires_at=row[5],
        revoked=bool(row[6]),
    )


_COLUMNS = "token_id, token_hash, role, scopes, created_at, expires_at, revoked"


def verify_token(conn: sqlite3.Connection, plaintext: str, *, now: Optional[datetime] = None) -> Optional[APIToken]:
    now = now or datetime.now(timezone.utc)
    row = conn.execute(
        f"SELECT {_COLUMNS} FROM api_tokens WHERE token_hash = ?", (_hash_token(plaintext),)
    ).fetchone()
    if row is None:
        return None
    token = _row_to_token(row)
    if token.revoked:
        return None
    if token.expires_at is not None and token.expires_at < now:
        return None
    return token


def revoke(conn: sqlite3.Connection, token_id: str) -> None:
    conn.execute("UPDATE api_tokens SET revoked = 1 WHERE token_id = ?", (token_id,))
    conn.commit()
