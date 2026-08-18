"""Cola persistente de entregas pendientes para destinos `api_push` con
`capacity.rate_limit_per_minute` configurado (spec/04-IOC-MODEL-POLICIES.md
"Capacidad y throughput por destino": "el worker respeta el limite y encola
el excedente; nunca lo descarta").

Separada del ledger (`hub/ledger.py`) a proposito: el ledger registra el
RESULTADO de un intento de entrega ya hecho (delivered/retrying/dead_letter/
etc.); esta tabla registra un evento que TODAVIA no se intento porque el
destino esta al tope de su tasa configurada -- una vez que `deliver()` se
llama de verdad (ver `drain_delivery_queues` en `hub/service.py`), el item
sale de aca y el ledger es la unica fuente de verdad sobre su resultado.

Persistente (no en memoria) para que un restart del proceso no pierda
trabajo encolado ni el conteo de la ventana de tasa actual.

Autor: Athan Espinoza
"""
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from hub.models import CanonicalIOCEvent
from hub.policy import ReasonCode


def init_db(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS delivery_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            destination_id TEXT NOT NULL,
            policy_version INTEGER NOT NULL,
            reason TEXT NOT NULL,
            event_json TEXT NOT NULL,
            enqueued_at TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_delivery_queue_destination ON delivery_queue(destination_id, id)")
    # Ventana fija de 1 minuto (no deslizante): simple y persistente en vez
    # de un token bucket en memoria. Imprecision aceptada en el borde de la
    # ventana -- el spec pide una tasa "por minuto", no un SLA exacto al
    # segundo.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS delivery_rate_window (
            destination_id TEXT PRIMARY KEY,
            window_start TEXT NOT NULL,
            sent_count INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.commit()
    return conn


@dataclass
class QueuedDelivery:
    id: int
    destination_id: str
    policy_version: int
    reason: ReasonCode
    event: CanonicalIOCEvent
    enqueued_at: datetime


def enqueue(
    conn,
    *,
    destination_id: str,
    policy_version: int,
    reason: ReasonCode,
    event: CanonicalIOCEvent,
    now: Optional[datetime] = None,
) -> int:
    now = now or datetime.now(timezone.utc)
    cur = conn.execute(
        "INSERT INTO delivery_queue (destination_id, policy_version, reason, event_json, enqueued_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (destination_id, policy_version, reason.value, event.model_dump_json(), now.isoformat()),
    )
    conn.commit()
    return cur.lastrowid


def count_pending(conn, destination_id: str) -> int:
    row = conn.execute("SELECT COUNT(*) FROM delivery_queue WHERE destination_id = ?", (destination_id,)).fetchone()
    return row[0] if row else 0


def _row_to_item(row) -> QueuedDelivery:
    id_, destination_id, policy_version, reason, event_json, enqueued_at = row
    return QueuedDelivery(
        id=id_,
        destination_id=destination_id,
        policy_version=policy_version,
        reason=ReasonCode(reason),
        event=CanonicalIOCEvent.model_validate_json(event_json),
        enqueued_at=datetime.fromisoformat(enqueued_at),
    )


def peek_batch(conn, destination_id: str, limit: int) -> list[QueuedDelivery]:
    # FIFO por `id` (autoincrement = orden de encolado). No borra el item --
    # eso es responsabilidad de `remove()`, llamado recien despues de que
    # `deliver()` ya lo proceso de verdad, para no perderlo si el proceso se
    # cae entre el peek y el intento real.
    if limit <= 0:
        return []
    rows = conn.execute(
        "SELECT id, destination_id, policy_version, reason, event_json, enqueued_at FROM delivery_queue "
        "WHERE destination_id = ? ORDER BY id ASC LIMIT ?",
        (destination_id, limit),
    ).fetchall()
    return [_row_to_item(r) for r in rows]


def remove(conn, queue_id: int) -> None:
    conn.execute("DELETE FROM delivery_queue WHERE id = ?", (queue_id,))
    conn.commit()


def list_destinations_with_pending(conn) -> list[str]:
    rows = conn.execute("SELECT DISTINCT destination_id FROM delivery_queue").fetchall()
    return [r[0] for r in rows]


def _window_key(now: datetime) -> str:
    return now.strftime("%Y-%m-%dT%H:%M")


def allowed_sends_this_minute(conn, destination_id: str, *, rate_limit_per_minute: int, now: Optional[datetime] = None) -> int:
    now = now or datetime.now(timezone.utc)
    window_start = _window_key(now)
    row = conn.execute(
        "SELECT window_start, sent_count FROM delivery_rate_window WHERE destination_id = ?", (destination_id,)
    ).fetchone()
    if row is None or row[0] != window_start:
        return rate_limit_per_minute
    return max(0, rate_limit_per_minute - row[1])


def record_sends(conn, destination_id: str, count: int, *, now: Optional[datetime] = None) -> None:
    if count <= 0:
        return
    now = now or datetime.now(timezone.utc)
    window_start = _window_key(now)
    row = conn.execute(
        "SELECT window_start, sent_count FROM delivery_rate_window WHERE destination_id = ?", (destination_id,)
    ).fetchone()
    if row is not None and row[0] == window_start:
        conn.execute(
            "UPDATE delivery_rate_window SET sent_count = sent_count + ? WHERE destination_id = ?",
            (count, destination_id),
        )
    else:
        conn.execute(
            "INSERT INTO delivery_rate_window (destination_id, window_start, sent_count) VALUES (?, ?, ?) "
            "ON CONFLICT(destination_id) DO UPDATE SET window_start = excluded.window_start, sent_count = excluded.sent_count",
            (destination_id, window_start, count),
        )
    conn.commit()
