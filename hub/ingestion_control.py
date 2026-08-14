"""Control de ingestion cross-proceso (spec/07-ADMIN-UI-ANGULAR.md "OpenCTI /
Ingesta": "pausar ingesta e iniciar reconciliacion... rebobinar/re-sincronizar").

`hub.service` (ingestion) y `hub.api` (Admin API) son procesos separados sin
comunicacion directa entre si. En vez de IPC, la API escribe una fila de
control en SQLite que `hub.service.listen_live_stream` sondea en su loop --
mismo principio que ya usan `hub/cursor_store.py`/`hub/ledger.py` para
compartir estado durable entre reinicios, aplicado aca entre procesos.
"""
import sqlite3
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel


class IngestionControl(BaseModel):
    source_id: str
    paused: bool = False
    reconcile_requested: bool = False
    rewind_to_cursor: Optional[str] = None
    rewind_reason: Optional[str] = None
    updated_at: datetime


def init_db(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ingestion_control (
            source_id TEXT PRIMARY KEY,
            paused INTEGER NOT NULL DEFAULT 0,
            reconcile_requested INTEGER NOT NULL DEFAULT 0,
            rewind_to_cursor TEXT,
            rewind_reason TEXT,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    return conn


_COLUMNS = "source_id, paused, reconcile_requested, rewind_to_cursor, rewind_reason, updated_at"


def _row_to_control(row) -> IngestionControl:
    return IngestionControl(
        source_id=row[0],
        paused=bool(row[1]),
        reconcile_requested=bool(row[2]),
        rewind_to_cursor=row[3],
        rewind_reason=row[4],
        updated_at=row[5],
    )


def get_control(conn: sqlite3.Connection, source_id: str) -> IngestionControl:
    """Siempre devuelve un control valido: si no existe fila todavia, el
    default es 'sin pausar, sin pedidos pendientes' (spec: el sistema arranca
    operativo, no pausado)."""
    row = conn.execute(f"SELECT {_COLUMNS} FROM ingestion_control WHERE source_id = ?", (source_id,)).fetchone()
    if row is None:
        return IngestionControl(source_id=source_id, updated_at=datetime.now(timezone.utc))
    return _row_to_control(row)


def _upsert(conn: sqlite3.Connection, control: IngestionControl) -> None:
    conn.execute(
        f"""
        INSERT INTO ingestion_control ({_COLUMNS})
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(source_id) DO UPDATE SET
            paused = excluded.paused,
            reconcile_requested = excluded.reconcile_requested,
            rewind_to_cursor = excluded.rewind_to_cursor,
            rewind_reason = excluded.rewind_reason,
            updated_at = excluded.updated_at
        """,
        (
            control.source_id,
            int(control.paused),
            int(control.reconcile_requested),
            control.rewind_to_cursor,
            control.rewind_reason,
            control.updated_at.isoformat(),
        ),
    )
    conn.commit()


def set_paused(conn: sqlite3.Connection, source_id: str, paused: bool, *, now: Optional[datetime] = None) -> IngestionControl:
    control = get_control(conn, source_id)
    control = control.model_copy(update={"paused": paused, "updated_at": now or datetime.now(timezone.utc)})
    _upsert(conn, control)
    return control


def request_reconcile(conn: sqlite3.Connection, source_id: str, *, now: Optional[datetime] = None) -> IngestionControl:
    control = get_control(conn, source_id)
    control = control.model_copy(update={"reconcile_requested": True, "updated_at": now or datetime.now(timezone.utc)})
    _upsert(conn, control)
    return control


def clear_reconcile_request(conn: sqlite3.Connection, source_id: str, *, now: Optional[datetime] = None) -> IngestionControl:
    control = get_control(conn, source_id)
    control = control.model_copy(update={"reconcile_requested": False, "updated_at": now or datetime.now(timezone.utc)})
    _upsert(conn, control)
    return control


def request_rewind(
    conn: sqlite3.Connection, source_id: str, *, cursor_value: str, reason: str, now: Optional[datetime] = None
) -> IngestionControl:
    control = get_control(conn, source_id)
    control = control.model_copy(
        update={"rewind_to_cursor": cursor_value, "rewind_reason": reason, "updated_at": now or datetime.now(timezone.utc)}
    )
    _upsert(conn, control)
    return control


def clear_rewind_request(conn: sqlite3.Connection, source_id: str, *, now: Optional[datetime] = None) -> IngestionControl:
    control = get_control(conn, source_id)
    control = control.model_copy(
        update={"rewind_to_cursor": None, "rewind_reason": None, "updated_at": now or datetime.now(timezone.utc)}
    )
    _upsert(conn, control)
    return control
