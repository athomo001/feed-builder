"""Persistencia de alertas: cada alerta guarda su severidad, estado y
timestamps, el componente/recurso afectado, y la condicion que la activo
con su valor observado. Las alertas deben evitar duplicacion mediante una
clave estable y usar cooldown para no inundar al operador. SQLite, mismo
estilo que `hub/api/audit_store.py`.

Clave de dedup estable = `(condition, component, resource_id)`, codificada
directo como `alert_id` (evita una tabla de lookup aparte): una alerta que
sigue firing solo actualiza `last_seen_at`/`observed_value`, nunca duplica
fila. Si la condicion deja de cumplirse, `resolve_alert` la cierra; una
condicion que vuelve a dispararse despues de resuelta abre un ciclo nuevo
(`first_seen_at` se reinicia).

Autor: Athan Espinoza
"""
import sqlite3
from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel

Severity = Literal["info", "warning", "critical"]
AlertState = Literal["firing", "acknowledged", "resolved"]


class Alert(BaseModel):
    alert_id: str
    condition: str
    severity: Severity
    state: AlertState
    component: str
    resource_id: str
    observed_value: Optional[str] = None
    first_seen_at: datetime
    last_seen_at: datetime
    last_notified_at: Optional[datetime] = None
    acknowledged_by: Optional[str] = None
    acknowledged_at: Optional[datetime] = None


def init_db(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS alerts (
            alert_id TEXT PRIMARY KEY,
            condition_name TEXT NOT NULL,
            severity TEXT NOT NULL,
            state TEXT NOT NULL,
            component TEXT NOT NULL,
            resource_id TEXT NOT NULL,
            observed_value TEXT,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            last_notified_at TEXT,
            acknowledged_by TEXT,
            acknowledged_at TEXT
        )
        """
    )
    conn.commit()
    return conn


# Orden canonico de columnas de `alerts`, compartido por cada INSERT/SELECT
# de este modulo para que el INSERT posicional y `_row_to_alert` (que lee
# por indice de tupla) nunca queden desalineados.
_COLUMNS = (
    "alert_id, condition_name, severity, state, component, resource_id, observed_value, "
    "first_seen_at, last_seen_at, last_notified_at, acknowledged_by, acknowledged_at"
)


def _row_to_alert(row) -> Alert:
    return Alert(
        alert_id=row[0],
        condition=row[1],
        severity=row[2],
        state=row[3],
        component=row[4],
        resource_id=row[5],
        observed_value=row[6],
        first_seen_at=row[7],
        last_seen_at=row[8],
        last_notified_at=row[9],
        acknowledged_by=row[10],
        acknowledged_at=row[11],
    )


def _save(conn: sqlite3.Connection, alert: Alert) -> None:
    conn.execute(
        f"""
        INSERT INTO alerts ({_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(alert_id) DO UPDATE SET
            severity = excluded.severity,
            state = excluded.state,
            observed_value = excluded.observed_value,
            first_seen_at = excluded.first_seen_at,
            last_seen_at = excluded.last_seen_at,
            last_notified_at = excluded.last_notified_at,
            acknowledged_by = excluded.acknowledged_by,
            acknowledged_at = excluded.acknowledged_at
        """,
        (
            alert.alert_id,
            alert.condition,
            alert.severity,
            alert.state,
            alert.component,
            alert.resource_id,
            alert.observed_value,
            alert.first_seen_at.isoformat(),
            alert.last_seen_at.isoformat(),
            alert.last_notified_at.isoformat() if alert.last_notified_at else None,
            alert.acknowledged_by,
            alert.acknowledged_at.isoformat() if alert.acknowledged_at else None,
        ),
    )
    conn.commit()


def get_alert(conn: sqlite3.Connection, alert_id: str) -> Optional[Alert]:
    row = conn.execute(f"SELECT {_COLUMNS} FROM alerts WHERE alert_id = ?", (alert_id,)).fetchone()
    return _row_to_alert(row) if row else None


def upsert_alert(
    conn: sqlite3.Connection,
    *,
    condition: str,
    severity: Severity,
    component: str,
    resource_id: str,
    observed_value: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Alert:
    now = now or datetime.now(timezone.utc)
    # alert_id = clave de dedup codificada directo (ver docstring del
    # modulo): mismo condition+component+resource_id siempre mapea al mismo
    # id, sin necesitar una tabla de lookup para encontrar la alerta previa.
    alert_id = f"{condition}:{component}:{resource_id}"
    existing = get_alert(conn, alert_id)
    if existing is not None and existing.state != "resolved":
        # Sigue firing: se actualiza la fila existente en vez de crear una
        # nueva, para no duplicar alertas por la misma condicion.
        updated = existing.model_copy(update={"severity": severity, "observed_value": observed_value, "last_seen_at": now})
    else:
        # No existe, o existe pero ya estaba resuelta: es un ciclo nuevo,
        # por eso first_seen_at se reinicia en vez de heredar el de antes.
        updated = Alert(
            alert_id=alert_id,
            condition=condition,
            severity=severity,
            state="firing",
            component=component,
            resource_id=resource_id,
            observed_value=observed_value,
            first_seen_at=now,
            last_seen_at=now,
        )
    _save(conn, updated)
    return updated


def resolve_alert(conn: sqlite3.Connection, alert_id: str, *, now: Optional[datetime] = None) -> Optional[Alert]:
    existing = get_alert(conn, alert_id)
    if existing is None or existing.state == "resolved":
        return existing
    updated = existing.model_copy(update={"state": "resolved", "last_seen_at": now or datetime.now(timezone.utc)})
    _save(conn, updated)
    return updated


def acknowledge_alert(conn: sqlite3.Connection, alert_id: str, *, actor: str, now: Optional[datetime] = None) -> Optional[Alert]:
    existing = get_alert(conn, alert_id)
    if existing is None:
        return None
    updated = existing.model_copy(
        update={"state": "acknowledged", "acknowledged_by": actor, "acknowledged_at": now or datetime.now(timezone.utc)}
    )
    _save(conn, updated)
    return updated


def mark_notified(conn: sqlite3.Connection, alert_id: str, *, now: Optional[datetime] = None) -> None:
    existing = get_alert(conn, alert_id)
    if existing is None:
        return
    _save(conn, existing.model_copy(update={"last_notified_at": now or datetime.now(timezone.utc)}))


def list_alerts(
    conn: sqlite3.Connection,
    *,
    condition: Optional[str] = None,
    severity: Optional[str] = None,
    state: Optional[str] = None,
    component: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> list[Alert]:
    clauses, params = [], []
    if condition is not None:
        clauses.append("condition_name = ?")
        params.append(condition)
    if severity is not None:
        clauses.append("severity = ?")
        params.append(severity)
    if state is not None:
        clauses.append("state = ?")
        params.append(state)
    if component is not None:
        clauses.append("component = ?")
        params.append(component)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.extend([limit, offset])
    rows = conn.execute(
        f"SELECT {_COLUMNS} FROM alerts {where} ORDER BY last_seen_at DESC LIMIT ? OFFSET ?", params
    ).fetchall()
    return [_row_to_alert(row) for row in rows]


def should_notify(alert: Alert, *, cooldown_seconds: int, now: Optional[datetime] = None) -> bool:
    """Una alerta nueva (nunca notificada) siempre notifica; una que sigue
    firing solo vuelve a notificar despues del cooldown (spec/06 "usar
    cooldown para no inundar al operador")."""
    if alert.last_notified_at is None:
        return True
    now = now or datetime.now(timezone.utc)
    return (now - alert.last_notified_at).total_seconds() >= cooldown_seconds
