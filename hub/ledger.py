"""Event/Delivery ledger: registra el estado de cada intento de entrega de un
evento a un destino, para trazabilidad y recuperacion tras fallas.

Clave de una entrega: event_id + destination_id + policy_version -- una
misma entrega puede reintentarse bajo una version de politica distinta si
la politica cambio entre intentos, y eso debe quedar como una fila separada
en vez de pisar el resultado del intento anterior.
SQLite para MVP single-node, misma decision documentada en hub/cursor_store.py.

Autor: Athan Espinoza
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
    attempts: int = 0


def init_db(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False)
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
            attempts INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (event_id, destination_id, policy_version)
        )
        """
    )
    try:
        # Migracion in-place: bases creadas antes de que existiera la
        # columna `attempts` no la tienen todavia. ALTER TABLE ADD COLUMN
        # falla si la columna ya existe, asi que el intento se envuelve en
        # try/except en vez de chequear el esquema primero -- es idempotente
        # y mas simple que inspeccionar PRAGMA table_info antes de decidir.
        conn.execute("ALTER TABLE event_ledger ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    conn.commit()
    return conn


# ON CONFLICT DO UPDATE en vez de INSERT-then-UPDATE separados: hace el
# upsert atomico frente a reintentos concurrentes del mismo (event_id,
# destination_id, policy_version), que es exactamente el caso de uso
# (un evento reintentado varias veces actualiza la misma fila cada vez).
def upsert_delivery(conn: sqlite3.Connection, entry: LedgerEntry) -> None:
    conn.execute(
        """
        INSERT INTO event_ledger
            (event_id, stix_id, destination_id, policy_version, state, reason, created_at, updated_at, error, attempts)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(event_id, destination_id, policy_version) DO UPDATE SET
            state = excluded.state,
            reason = excluded.reason,
            updated_at = excluded.updated_at,
            error = excluded.error,
            attempts = excluded.attempts
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
            entry.attempts,
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
        attempts=row[9],
    )


# Orden canonico de columnas de event_ledger, compartido por cada INSERT/
# SELECT de este modulo: al definirlo una sola vez, un SELECT siempre trae
# las columnas en el mismo orden que espera `_row_to_entry` (por indice de
# tupla, no por nombre) y un ALTER TABLE que agregue una columna nueva solo
# necesita actualizarse aca, no en cada query dispersa por el archivo.
_COLUMNS = (
    "event_id, stix_id, destination_id, policy_version, state, reason, created_at, updated_at, error, attempts"
)


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


def list_seen_stix_ids(conn: sqlite3.Connection) -> set:
    """Recuperacion tras reinicio: que objetos STIX ya paso el Hub por el
    ledger, usado para detectar brechas sin tener que reprocesar todo el
    historial en memoria."""
    rows = conn.execute("SELECT DISTINCT stix_id FROM event_ledger").fetchall()
    return {row[0] for row in rows}


def search_deliveries(
    conn: sqlite3.Connection,
    *,
    event_id: Optional[str] = None,
    stix_id: Optional[str] = None,
    destination_id: Optional[str] = None,
    state: Optional[DeliveryState] = None,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    limit: int = 50,
    offset: int = 0,
) -> list[LedgerEntry]:
    """Inspector del Event Ledger para busqueda por event_id, stix_id,
    destino o fecha. Acotado a las columnas que el ledger ya guarda -- no
    hay un Canonical Event Store separado todavia, asi que no se puede
    filtrar por family/subtype/valor del IOC aca, solo por lo que el ledger
    de entregas registra."""
    clauses, params = [], []
    if event_id is not None:
        clauses.append("event_id = ?")
        params.append(event_id)
    if stix_id is not None:
        clauses.append("stix_id = ?")
        params.append(stix_id)
    if destination_id is not None:
        clauses.append("destination_id = ?")
        params.append(destination_id)
    if state is not None:
        clauses.append("state = ?")
        params.append(state.value)
    if since is not None:
        clauses.append("created_at >= ?")
        params.append(since.isoformat())
    if until is not None:
        clauses.append("created_at <= ?")
        params.append(until.isoformat())

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.extend([limit, offset])
    rows = conn.execute(
        f"SELECT {_COLUMNS} FROM event_ledger {where} ORDER BY created_at DESC LIMIT ? OFFSET ?", params
    ).fetchall()
    return [_row_to_entry(row) for row in rows]


def list_dead_letters(conn: sqlite3.Connection, *, destination_id: Optional[str] = None) -> list[LedgerEntry]:
    """Entregas que agotaron reintentos y quedaron en dead-letter, para que
    un operador las revise/reintente manualmente."""
    if destination_id:
        rows = conn.execute(
            f"SELECT {_COLUMNS} FROM event_ledger WHERE state = ? AND destination_id = ?",
            (DeliveryState.DEAD_LETTER.value, destination_id),
        ).fetchall()
    else:
        rows = conn.execute(
            f"SELECT {_COLUMNS} FROM event_ledger WHERE state = ?", (DeliveryState.DEAD_LETTER.value,)
        ).fetchall()
    return [_row_to_entry(row) for row in rows]
