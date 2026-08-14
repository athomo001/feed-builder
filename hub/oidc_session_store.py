"""Sesiones OIDC. Mismo patron que `hub/api/token_store.py`: el id de
sesion (el valor de la cookie `hub_session`) se genera con
`secrets.token_urlsafe` y solo su hash SHA-256 se persiste -- una fuga de
la base no permite reconstruir sesiones validas.

Autor: Athan Espinoza
"""
import hashlib
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Optional

from pydantic import BaseModel

from hub.api.token_store import Role


class OIDCSession(BaseModel):
    # `session_id` es un identificador no secreto (aparece en auditoria, ver
    # `hub/api/auth.py`); el secreto real es el `plaintext` que recibe el
    # navegador en la cookie -- este modelo nunca lo guarda, solo su hash en
    # `session_hash`.
    session_id: str
    session_hash: str
    subject: str
    role: Role
    created_at: datetime
    expires_at: datetime
    revoked: bool = False


def _hash_session_id(plaintext: str) -> str:
    # SHA-256 simple (no un KDF costoso como bcrypt/argon2) porque la
    # entrada ya es un secreto de alta entropia generado por
    # `secrets.token_urlsafe`, no una contrasena de baja entropia adivinable
    # por fuerza bruta.
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def init_db(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS oidc_sessions (
            session_id TEXT PRIMARY KEY,
            session_hash TEXT NOT NULL UNIQUE,
            subject TEXT NOT NULL,
            role TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            revoked INTEGER NOT NULL
        )
        """
        # UNIQUE en session_hash: dos sesiones nunca deberian mapear al
        # mismo hash; si eso pasara (bug o colision) la base lo rechaza en
        # vez de dejar que una sesion pise silenciosamente a otra.
    )
    conn.commit()
    return conn


def create_session(
    conn: sqlite3.Connection, *, subject: str, role: Role, ttl_seconds: int, now: Optional[datetime] = None
) -> tuple[OIDCSession, str]:
    now = now or datetime.now(timezone.utc)
    # El valor que viajara en la cookie del navegador existe en claro solo
    # aca, en este momento; se devuelve al llamador pero nunca se persiste
    # -- lo que queda en la base es unicamente su hash (`session_hash`).
    plaintext = secrets.token_urlsafe(32)
    session = OIDCSession(
        session_id=secrets.token_hex(8),
        session_hash=_hash_session_id(plaintext),
        subject=subject,
        role=role,
        created_at=now,
        expires_at=now + timedelta(seconds=ttl_seconds),
        revoked=False,
    )
    conn.execute(
        "INSERT INTO oidc_sessions (session_id, session_hash, subject, role, created_at, expires_at, revoked) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            session.session_id,
            session.session_hash,
            session.subject,
            session.role,
            session.created_at.isoformat(),
            session.expires_at.isoformat(),
            int(session.revoked),
        ),
    )
    conn.commit()
    return session, plaintext


# Orden de columnas explicito (en vez de `SELECT *`) para que siempre
# coincida con los indices posicionales que usa `_row_to_session`.
_COLUMNS = "session_id, session_hash, subject, role, created_at, expires_at, revoked"


def _row_to_session(row) -> OIDCSession:
    return OIDCSession(
        session_id=row[0],
        session_hash=row[1],
        subject=row[2],
        role=row[3],
        created_at=row[4],
        expires_at=row[5],
        revoked=bool(row[6]),
    )


def verify_session(conn: sqlite3.Connection, plaintext: str, *, now: Optional[datetime] = None) -> Optional[OIDCSession]:
    now = now or datetime.now(timezone.utc)
    row = conn.execute(
        f"SELECT {_COLUMNS} FROM oidc_sessions WHERE session_hash = ?", (_hash_session_id(plaintext),)
    ).fetchone()
    if row is None:
        return None
    session = _row_to_session(row)
    # Revocada o expirada se tratan igual, como "sesion invalida": el
    # llamador no necesita distinguir el motivo, y no exponer la diferencia
    # evita dar pistas sobre el estado interno de la sesion a quien presente
    # una cookie robada o vieja.
    if session.revoked or session.expires_at < now:
        return None
    return session


def revoke_session(conn: sqlite3.Connection, plaintext: str) -> None:
    # UPDATE en vez de DELETE: conserva el registro (util para trazabilidad)
    # aunque la sesion deje de ser valida para `verify_session`.
    conn.execute("UPDATE oidc_sessions SET revoked = 1 WHERE session_hash = ?", (_hash_session_id(plaintext),))
    conn.commit()
