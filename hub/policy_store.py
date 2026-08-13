"""CRUD y ciclo de vida de politicas versionadas (spec/03-ARCHITECTURE.md
"Policy engine": "Evalua politicas inmutables por version"; spec/04
"Ejemplo de politica por destino"; Entrega 2 "simulacion, publicacion y
rollback").

El CONTENIDO de una version nunca cambia una vez creada; lo unico que
transiciona es `status`. Publicar/rollback nunca borra historial (spec/07
"No borrar eventos del ledger al resetear el cursor" aplica por analogia
aqui: tampoco se borran versiones de politica).
"""
import json
import sqlite3
from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field

PolicyStatus = Literal["draft", "published", "superseded", "rolled_back"]


class AllowedIOC(BaseModel):
    family: str
    subtypes: list[str]


class PolicyVersion(BaseModel):
    policy_id: str
    version: int
    destination_id: str
    allowed_iocs: list[AllowedIOC] = Field(default_factory=list)
    ttl_days: dict[str, int] = Field(default_factory=dict)  # subtype -> dias
    status: PolicyStatus = "draft"
    created_at: datetime
    published_at: Optional[datetime] = None


def init_db(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS policy_versions (
            policy_id TEXT NOT NULL,
            version INTEGER NOT NULL,
            destination_id TEXT NOT NULL,
            allowed_iocs TEXT NOT NULL,
            ttl_days TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            published_at TEXT,
            PRIMARY KEY (policy_id, version)
        )
        """
    )
    conn.commit()
    return conn


_COLUMNS = "policy_id, version, destination_id, allowed_iocs, ttl_days, status, created_at, published_at"


def _row_to_version(row) -> PolicyVersion:
    return PolicyVersion(
        policy_id=row[0],
        version=row[1],
        destination_id=row[2],
        allowed_iocs=[AllowedIOC(**x) for x in json.loads(row[3])],
        ttl_days=json.loads(row[4]),
        status=row[5],
        created_at=row[6],
        published_at=row[7],
    )


def _insert(conn: sqlite3.Connection, pv: PolicyVersion) -> None:
    conn.execute(
        f"INSERT INTO policy_versions ({_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            pv.policy_id,
            pv.version,
            pv.destination_id,
            json.dumps([a.model_dump() for a in pv.allowed_iocs]),
            json.dumps(pv.ttl_days),
            pv.status,
            pv.created_at.isoformat(),
            pv.published_at.isoformat() if pv.published_at else None,
        ),
    )
    conn.commit()


def _update_status(conn: sqlite3.Connection, policy_id: str, version: int, *, status: str, published_at=None) -> None:
    conn.execute(
        "UPDATE policy_versions SET status = ?, published_at = ? WHERE policy_id = ? AND version = ?",
        (status, published_at.isoformat() if published_at else None, policy_id, version),
    )
    conn.commit()


def create_draft(
    conn: sqlite3.Connection,
    *,
    policy_id: str,
    destination_id: str,
    allowed_iocs: list[AllowedIOC],
    ttl_days: dict,
    now: Optional[datetime] = None,
) -> PolicyVersion:
    now = now or datetime.now(timezone.utc)
    row = conn.execute(
        "SELECT COALESCE(MAX(version), 0) FROM policy_versions WHERE policy_id = ?", (policy_id,)
    ).fetchone()
    next_version = (row[0] or 0) + 1

    pv = PolicyVersion(
        policy_id=policy_id,
        version=next_version,
        destination_id=destination_id,
        allowed_iocs=allowed_iocs,
        ttl_days=ttl_days,
        status="draft",
        created_at=now,
    )
    _insert(conn, pv)
    return pv


def list_policy_ids(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("SELECT DISTINCT policy_id FROM policy_versions ORDER BY policy_id").fetchall()
    return [row[0] for row in rows]


def list_versions(conn: sqlite3.Connection, policy_id: str) -> list[PolicyVersion]:
    rows = conn.execute(
        f"SELECT {_COLUMNS} FROM policy_versions WHERE policy_id = ? ORDER BY version", (policy_id,)
    ).fetchall()
    return [_row_to_version(row) for row in rows]


def get_version(conn: sqlite3.Connection, policy_id: str, version: int) -> Optional[PolicyVersion]:
    row = conn.execute(
        f"SELECT {_COLUMNS} FROM policy_versions WHERE policy_id = ? AND version = ?", (policy_id, version)
    ).fetchone()
    return _row_to_version(row) if row else None


def get_active_version(conn: sqlite3.Connection, policy_id: str) -> Optional[PolicyVersion]:
    row = conn.execute(
        f"SELECT {_COLUMNS} FROM policy_versions WHERE policy_id = ? AND status = 'published'", (policy_id,)
    ).fetchone()
    return _row_to_version(row) if row else None


def get_active_version_for_destination(conn: sqlite3.Connection, destination_id: str) -> Optional[PolicyVersion]:
    row = conn.execute(
        f"SELECT {_COLUMNS} FROM policy_versions WHERE destination_id = ? AND status = 'published'",
        (destination_id,),
    ).fetchone()
    return _row_to_version(row) if row else None


def publish(conn: sqlite3.Connection, policy_id: str, version: int, *, now: Optional[datetime] = None) -> PolicyVersion:
    now = now or datetime.now(timezone.utc)
    target = get_version(conn, policy_id, version)
    if target is None:
        raise ValueError(f"policy '{policy_id}' version {version} no existe")

    current = get_active_version(conn, policy_id)
    if current is not None and current.version != version:
        _update_status(conn, policy_id, current.version, status="superseded")

    _update_status(conn, policy_id, version, status="published", published_at=now)
    return get_version(conn, policy_id, version)


def rollback(conn: sqlite3.Connection, policy_id: str, version: int, *, now: Optional[datetime] = None) -> PolicyVersion:
    """Reactiva una version previa sin alterar su contenido (spec/09
    decision #: rollback via UI/API con rol security-admin en la spec de UI;
    aqui se expone via policy-admin, ver spec/08 roles)."""
    now = now or datetime.now(timezone.utc)
    target = get_version(conn, policy_id, version)
    if target is None:
        raise ValueError(f"policy '{policy_id}' version {version} no existe")

    current = get_active_version(conn, policy_id)
    if current is not None and current.version != version:
        _update_status(conn, policy_id, current.version, status="rolled_back")

    _update_status(conn, policy_id, version, status="published", published_at=now)
    return get_version(conn, policy_id, version)
