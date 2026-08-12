"""Cursor store durable (spec/03-ARCHITECTURE.md "Ingestion service", Entrega 1).

SQLite: valido para MVP single-node segun spec/03 "Persistencia" (la eleccion
entre SQLite y PostgreSQL para produccion sigue como decision abierta en
spec/09, punto 2; esto no la prejuzga, solo implementa el contrato de
durabilidad con lo que ya esta disponible en la stdlib).
"""
import sqlite3
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel


class CursorState(BaseModel):
    source_id: str
    cursor_value: str
    updated_at: datetime


def init_db(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS cursor_state (
            source_id TEXT PRIMARY KEY,
            cursor_value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    return conn


def save_cursor(conn: sqlite3.Connection, source_id: str, cursor_value: str, updated_at: Optional[datetime] = None) -> None:
    updated_at = updated_at or datetime.now(timezone.utc)
    conn.execute(
        """
        INSERT INTO cursor_state (source_id, cursor_value, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(source_id) DO UPDATE SET
            cursor_value = excluded.cursor_value,
            updated_at = excluded.updated_at
        """,
        (source_id, cursor_value, updated_at.isoformat()),
    )
    conn.commit()


def load_cursor(conn: sqlite3.Connection, source_id: str) -> Optional[CursorState]:
    row = conn.execute(
        "SELECT source_id, cursor_value, updated_at FROM cursor_state WHERE source_id = ?",
        (source_id,),
    ).fetchone()
    if row is None:
        return None
    return CursorState(source_id=row[0], cursor_value=row[1], updated_at=row[2])
