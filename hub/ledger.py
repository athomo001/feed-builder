"""Event/Delivery ledger (spec/03-ARCHITECTURE.md "Delivery ledger", spec/06
"Almacenamiento interno de metricas y trazabilidad", Entrega 1).

Clave de una entrega: event_id + destination_id + policy_version (spec/03).
SQLite para MVP single-node, misma decision documentada en hub/cursor_store.py.
"""
import sqlite3
from datetime import datetime
from typing import Optional

from pydantic import BaseModel

from hub.delivery import DeliveryState
from hub.policy import ReasonCode


class LedgerEntry(BaseModel):
    event_id: str
    stix_id: str
    destination_id: str
    policy_version: int
    state: DeliveryState
    reason: ReasonCode = ReasonCode.OK
    created_at: datetime
    updated_at: datetime
    error: Optional[str] = None


def init_db(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS event_ledger (
            event_id TEXT NOT NULL,
            stix_id TEXT NOT NULL,
            destination_id TEXT NOT NULL,
            policy_version INTEGER NOT NULL,
            state TEXT NOT NULL,
            reason TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            error TEXT,
            PRIMARY KEY (event_id, destination_id, policy_version)
        )
        """
    )
    conn.commit()
    return conn


def upsert_delivery(conn: sqlite3.Connection, entry: LedgerEntry) -> None:
    conn.execute(
        """
        INSERT INTO event_ledger
            (event_id, stix_id, destination_id, policy_version, state, reason, created_at, updated_at, error)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(event_id, destination_id, policy_version) DO UPDATE SET
            state = excluded.state,
            reason = excluded.reason,
            updated_at = excluded.updated_at,
            error = excluded.error
        """,
        (
            entry.event_id,
            entry.stix_id,
            entry.destination_id,
            entry.policy_version,
            entry.state.value,
            entry.reason.value,
            entry.created_at.isoformat(),
            entry.updated_at.isoformat(),
            entry.error,
        ),
    )
    conn.commit()


def _row_to_entry(row) -> LedgerEntry:
    return LedgerEntry(
        event_id=row[0],
        stix_id=row[1],
        destination_id=row[2],
        policy_version=row[3],
        state=row[4],
        reason=row[5],
        created_at=row[6],
        updated_at=row[7],
        error=row[8],
    )


_COLUMNS = "event_id, stix_id, destination_id, policy_version, state, reason, created_at, updated_at, error"


def get_delivery(conn: sqlite3.Connection, event_id: str, destination_id: str, policy_version: int) -> Optional[LedgerEntry]:
    row = conn.execute(
        f"SELECT {_COLUMNS} FROM event_ledger WHERE event_id = ? AND destination_id = ? AND policy_version = ?",
        (event_id, destination_id, policy_version),
    ).fetchone()
    return _row_to_entry(row) if row else None


def list_deliveries_for_event(conn: sqlite3.Connection, event_id: str) -> list[LedgerEntry]:
    rows = conn.execute(
        f"SELECT {_COLUMNS} FROM event_ledger WHERE event_id = ?",
        (event_id,),
    ).fetchall()
    return [_row_to_entry(row) for row in rows]
