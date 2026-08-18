"""CRUD y ciclo de vida de politicas versionadas.

El CONTENIDO de una version nunca cambia una vez creada; lo unico que
transiciona es `status`. Publicar/rollback nunca borra historial, por la
misma razon por la que no se borran eventos del ledger al resetear un
cursor: preservar la capacidad de auditar que politica exacta decidio cada
evento en el pasado.

Autor: Athan Espinoza
"""
import json
import sqlite3
from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field

# Transiciones validas: draft -> published -> superseded (reemplazada por
# otra publicacion) o rolled_back (reemplazada por un rollback). Se
# distinguen esos dos ultimos estados en vez de un unico "inactive" para que
# el historial permita reconstruir por que dejo de estar activa cada version.
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
    # subtype -> cantidad maxima de IOCs vigentes de ese subtipo (0/ausente =
    # sin tope propio, solo rige el default del destino). Mismo mecanismo de
    # truncado por prioridad que destination.capacity.max_records_per_file
    # (hub/txt_feed.py::FeedWriter.rebuild, overflow_strategy) -- el mas
    # viejo se descarta cuando se llena el cupo, independiente de si el TTL
    # todavia no vencio. Vive en la politica (no en el destino) porque, a
    # diferencia del limite de archivo (una constante tecnica del
    # consumidor), esta cantidad es una decision de negocio por tipo de IOC
    # -- pedido explicito del operador (2026-08-18): "TTL de 2 dias y
    # cantidad de 500, el hash mas antiguo se va en 2 dias o cuando se llenen
    # los 500, lo que pase primero".
    max_records: dict[str, int] = Field(default_factory=dict)
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
            max_records TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            published_at TEXT,
            PRIMARY KEY (policy_id, version)
        )
        """
    )
    # Migracion in-place para bases creadas antes de esta columna: SQLite no
    # soporta "ADD COLUMN IF NOT EXISTS", asi que se chequea el esquema real.
    existing_columns = {row[1] for row in conn.execute("PRAGMA table_info(policy_versions)").fetchall()}
    if "max_records" not in existing_columns:
        conn.execute("ALTER TABLE policy_versions ADD COLUMN max_records TEXT NOT NULL DEFAULT '{}'")
    conn.commit()
    return conn


_COLUMNS = "policy_id, version, destination_id, allowed_iocs, ttl_days, max_records, status, created_at, published_at"


def _row_to_version(row) -> PolicyVersion:
    return PolicyVersion(
        policy_id=row[0],
        version=row[1],
        destination_id=row[2],
        allowed_iocs=[AllowedIOC(**x) for x in json.loads(row[3])],
        ttl_days=json.loads(row[4]),
        max_records=json.loads(row[5]),
        status=row[6],
        created_at=row[7],
        published_at=row[8],
    )


def _insert(conn: sqlite3.Connection, pv: PolicyVersion) -> None:
    conn.execute(
        f"INSERT INTO policy_versions ({_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            pv.policy_id,
            pv.version,
            pv.destination_id,
            json.dumps([a.model_dump() for a in pv.allowed_iocs]),
            json.dumps(pv.ttl_days),
            json.dumps(pv.max_records),
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
    max_records: Optional[dict] = None,
    now: Optional[datetime] = None,
) -> PolicyVersion:
    now = now or datetime.now(timezone.utc)
    # La version siguiente se deriva del maximo existente (no de un contador
    # separado) para que sea monotonamente creciente por policy_id incluso si
    # versiones anteriores fueron superseded/rolled_back.
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
        max_records=max_records or {},
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

    # La version activa anterior pasa a "superseded" (no se borra) para que
    # el historial siga siendo consultable despues de publicar la nueva.
    current = get_active_version(conn, policy_id)
    if current is not None and current.version != version:
        _update_status(conn, policy_id, current.version, status="superseded")

    _update_status(conn, policy_id, version, status="published", published_at=now)
    return get_version(conn, policy_id, version)


def delete_draft_version(conn: sqlite3.Connection, policy_id: str, version: int) -> None:
    """Borra una version que TODAVIA nunca se publico. A diferencia de
    publish/rollback (que preservan historial a proposito, ver docstring del
    modulo), un draft nunca aparecio en ninguna decision real del ledger --
    borrarlo no pierde ningun registro de auditoria, solo saca un borrador
    que un operador ya no quiere. Por eso el borrado se restringe a
    status == 'draft': cualquier version que alguna vez estuvo publicada
    (published/superseded/rolled_back) queda referenciada por
    policy_version en filas del ledger y borrarla rompería esa trazabilidad.
    """
    target = get_version(conn, policy_id, version)
    if target is None:
        raise ValueError(f"policy '{policy_id}' version {version} no existe")
    if target.status != "draft":
        raise ValueError(f"policy '{policy_id}' version {version} esta en estado '{target.status}', solo se puede borrar un draft")
    conn.execute("DELETE FROM policy_versions WHERE policy_id = ? AND version = ?", (policy_id, version))
    conn.commit()


def delete_policy(conn: sqlite3.Connection, policy_id: str) -> None:
    """Borra TODAS las versiones de una politica, sin importar su estado --
    pedido explicitamente por el operador (2026-08-18): "si quiero la borro y
    hago una nueva". A diferencia de `delete_draft_version` (que protege el
    historial de auditoria de una version que estuvo activa), esto es un
    borrado real e irreversible: si alguna de esas versiones estuvo publicada
    alguna vez, las filas del ledger que la referencian por policy_version
    quedan apuntando a una version que ya no existe. Es la contrapartida
    explicita de simplicidad que el operador eligio sobre preservar ese
    historial.
    """
    conn.execute("DELETE FROM policy_versions WHERE policy_id = ?", (policy_id,))
    conn.commit()


def rollback(conn: sqlite3.Connection, policy_id: str, version: int, *, now: Optional[datetime] = None) -> PolicyVersion:
    """Reactiva una version previa sin alterar su contenido."""
    now = now or datetime.now(timezone.utc)
    target = get_version(conn, policy_id, version)
    if target is None:
        raise ValueError(f"policy '{policy_id}' version {version} no existe")

    # A diferencia de `publish`, aqui la version reemplazada se marca
    # "rolled_back" (no "superseded"): son la misma mecanica de reemplazo,
    # pero el motivo del cambio queda registrado de forma distinta para el
    # historial/auditoria.
    current = get_active_version(conn, policy_id)
    if current is not None and current.version != version:
        _update_status(conn, policy_id, current.version, status="rolled_back")

    _update_status(conn, policy_id, version, status="published", published_at=now)
    return get_version(conn, policy_id, version)
