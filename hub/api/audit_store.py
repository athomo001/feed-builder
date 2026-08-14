"""Registro historico de acciones de operadores (creacion de politicas,
reintentos/descartes de DLQ, cambios de destino, rebobinado de cursor),
buscable por actor, accion, recurso y fecha. Cada entrada guarda actor,
accion, recurso, valores anteriores/nuevos, motivo, resultado,
correlation_id y timestamp.

`before`/`after` nunca deben llevar secretos: los recursos auditados en este
proyecto (destinos, politicas, entregas) solo exponen `credential_ref`
(una referencia, no el secreto en si -- ver hub/credentials.py), asi que no
hace falta redaccion adicional aca.

Autor: Athan Espinoza
"""
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel

Result = Literal["success", "failure"]


class AuditEntry(BaseModel):
    audit_id: str
    actor_token_id: Optional[str] = None
    actor_role: Optional[str] = None
    action: str
    resource_type: str
    resource_id: str
    before: Optional[dict] = None
    after: Optional[dict] = None
    reason: Optional[str] = None
    result: Result = "success"
    correlation_id: Optional[str] = None
    created_at: datetime


def init_db(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_log (
            audit_id TEXT PRIMARY KEY,
            actor_token_id TEXT,
            actor_role TEXT,
            action TEXT NOT NULL,
            resource_type TEXT NOT NULL,
            resource_id TEXT NOT NULL,
            before TEXT,
            after TEXT,
            reason TEXT,
            result TEXT NOT NULL,
            correlation_id TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    return conn


_COLUMNS = (
    "audit_id, actor_token_id, actor_role, action, resource_type, resource_id, "
    "before, after, reason, result, correlation_id, created_at"
)


def _row_to_entry(row) -> AuditEntry:
    return AuditEntry(
        audit_id=row[0],
        actor_token_id=row[1],
        actor_role=row[2],
        action=row[3],
        resource_type=row[4],
        resource_id=row[5],
        before=json.loads(row[6]) if row[6] else None,
        after=json.loads(row[7]) if row[7] else None,
        reason=row[8],
        result=row[9],
        correlation_id=row[10],
        created_at=row[11],
    )


def record(
    # `audit_id` se genera aca (uuid4) en vez de dejar que SQLite lo asigne,
    # para poder devolverselo al caller (y usarlo como resource_id de logs
    # relacionados) sin necesitar una segunda consulta tras el INSERT.
    conn: sqlite3.Connection,
    *,
    actor_token_id: Optional[str],
    actor_role: Optional[str],
    action: str,
    resource_type: str,
    resource_id: str,
    before: Optional[dict] = None,
    after: Optional[dict] = None,
    reason: Optional[str] = None,
    result: Result = "success",
    correlation_id: Optional[str] = None,
    now: Optional[datetime] = None,
) -> AuditEntry:
    entry = AuditEntry(
        audit_id=str(uuid.uuid4()),
        actor_token_id=actor_token_id,
        actor_role=actor_role,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        before=before,
        after=after,
        reason=reason,
        result=result,
        correlation_id=correlation_id,
        created_at=now or datetime.now(timezone.utc),
    )
    conn.execute(
        f"INSERT INTO audit_log ({_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            entry.audit_id,
            entry.actor_token_id,
            entry.actor_role,
            entry.action,
            entry.resource_type,
            entry.resource_id,
            json.dumps(entry.before) if entry.before is not None else None,
            json.dumps(entry.after) if entry.after is not None else None,
            entry.reason,
            entry.result,
            entry.correlation_id,
            entry.created_at.isoformat(),
        ),
    )
    conn.commit()
    return entry


def list_audit(
    conn: sqlite3.Connection,
    *,
    actor_token_id: Optional[str] = None,
    action: Optional[str] = None,
    resource_type: Optional[str] = None,
    resource_id: Optional[str] = None,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    limit: int = 50,
    offset: int = 0,
) -> list[AuditEntry]:
    # Se arma el WHERE dinamicamente en vez de tener una query fija: todos
    # los filtros son opcionales y se pueden combinar libremente, asi que
    # una sola query con clausulas condicionales cubre cualquier combinacion
    # sin necesitar una variante SQL por cada subconjunto de filtros.
    clauses, params = [], []
    if actor_token_id is not None:
        clauses.append("actor_token_id = ?")
        params.append(actor_token_id)
    if action is not None:
        clauses.append("action = ?")
        params.append(action)
    if resource_type is not None:
        clauses.append("resource_type = ?")
        params.append(resource_type)
    if resource_id is not None:
        clauses.append("resource_id = ?")
        params.append(resource_id)
    if since is not None:
        clauses.append("created_at >= ?")
        params.append(since.isoformat())
    if until is not None:
        clauses.append("created_at <= ?")
        params.append(until.isoformat())

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.extend([limit, offset])
    rows = conn.execute(
        f"SELECT {_COLUMNS} FROM audit_log {where} ORDER BY created_at DESC LIMIT ? OFFSET ?", params
    ).fetchall()
    return [_row_to_entry(row) for row in rows]
