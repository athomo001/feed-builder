"""CRUD de destinos: alta, baja, pausa y consulta de los destinos a los que
el Hub distribuye IOC.

SQLite, mismo estilo que `hub/cursor_store.py`/`hub/ledger.py`: modelo
pydantic, columnas JSON para los campos anidados (capacity/retry/
format_options/allowed_ioc_types) porque su forma varia segun el adapter
(por ejemplo `capacity` cambia de shape segun `capacity["mode"]`) y no vale
la pena modelar cada variante como columnas propias en la tabla.

Autor: Athan Espinoza
"""
import json
import sqlite3
from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field

AdapterType = Literal[
    "txt_feed",
    "http_push",
    # Variantes file_feed con formato propio (Check Point CSV, MikroTik
    # .rsc, Wazuh CDB, STIX 2.1 bundle) y los dos api_push/servidor de alto
    # esfuerzo (QRadar Reference Set API, servidor TAXII 2.1 propio para que
    # un cliente TAXII externo haga poll).
    "csv_feed",
    "mikrotik_rsc",
    "wazuh_cdb",
    "stix_bundle_feed",
    "qradar_reference_set",
    "taxii2",
]


class RetryPolicy(BaseModel):
    max_attempts: int = 8
    backoff: Literal["exponential-jitter"] = "exponential-jitter"


class Destination(BaseModel):
    destination_id: str
    name: str
    adapter: AdapterType
    enabled: bool = True
    paused: bool = False
    endpoint: Optional[str] = None
    credential_ref: Optional[str] = None
    format: str = "txt"
    allowed_ioc_types: list[str] = Field(default_factory=list)  # "family/subtype"
    format_options: dict = Field(default_factory=dict)
    capacity: dict = Field(default_factory=dict)  # shape depende de capacity["mode"]
    supports_delete: bool = False
    delete_strategy: Optional[str] = None
    timeout_seconds: int = 15
    retry: RetryPolicy = Field(default_factory=RetryPolicy)
    created_at: datetime
    updated_at: datetime


def init_db(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS destinations (
            destination_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            adapter TEXT NOT NULL,
            enabled INTEGER NOT NULL,
            paused INTEGER NOT NULL,
            endpoint TEXT,
            credential_ref TEXT,
            format TEXT NOT NULL,
            allowed_ioc_types TEXT NOT NULL,
            format_options TEXT NOT NULL,
            capacity TEXT NOT NULL,
            supports_delete INTEGER NOT NULL,
            delete_strategy TEXT,
            timeout_seconds INTEGER NOT NULL,
            retry TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    return conn


def _row_to_destination(row) -> Destination:
    return Destination(
        destination_id=row[0],
        name=row[1],
        adapter=row[2],
        enabled=bool(row[3]),
        paused=bool(row[4]),
        endpoint=row[5],
        credential_ref=row[6],
        format=row[7],
        allowed_ioc_types=json.loads(row[8]),
        format_options=json.loads(row[9]),
        capacity=json.loads(row[10]),
        supports_delete=bool(row[11]),
        delete_strategy=row[12],
        timeout_seconds=row[13],
        retry=RetryPolicy(**json.loads(row[14])),
        created_at=row[15],
        updated_at=row[16],
    )


# Orden canonico de columnas de la tabla `destinations`, compartido por
# cada INSERT/SELECT de este modulo. Existe para que el INSERT (que arma la
# fila con "?" posicionales) y `_row_to_destination` (que lee la fila por
# indice de tupla, no por nombre) nunca se desincronicen: cambiar el orden o
# agregar una columna solo requiere tocar esta constante y el esquema de
# CREATE TABLE, no cada query individual del archivo.
_COLUMNS = (
    "destination_id, name, adapter, enabled, paused, endpoint, credential_ref, format, "
    "allowed_ioc_types, format_options, capacity, supports_delete, delete_strategy, "
    "timeout_seconds, retry, created_at, updated_at"
)


# ON CONFLICT DO UPDATE: upsert atomico por destination_id, usado tanto
# para creacion como para edicion -- el llamador no necesita distinguir
# "crear" de "actualizar", siempre pasa el objeto completo.
def upsert_destination(conn: sqlite3.Connection, destination: Destination) -> None:
    conn.execute(
        f"""
        INSERT INTO destinations ({_COLUMNS})
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(destination_id) DO UPDATE SET
            name = excluded.name,
            adapter = excluded.adapter,
            enabled = excluded.enabled,
            paused = excluded.paused,
            endpoint = excluded.endpoint,
            credential_ref = excluded.credential_ref,
            format = excluded.format,
            allowed_ioc_types = excluded.allowed_ioc_types,
            format_options = excluded.format_options,
            capacity = excluded.capacity,
            supports_delete = excluded.supports_delete,
            delete_strategy = excluded.delete_strategy,
            timeout_seconds = excluded.timeout_seconds,
            retry = excluded.retry,
            updated_at = excluded.updated_at
        """,
        (
            destination.destination_id,
            destination.name,
            destination.adapter,
            int(destination.enabled),
            int(destination.paused),
            destination.endpoint,
            destination.credential_ref,
            destination.format,
            json.dumps(destination.allowed_ioc_types),
            json.dumps(destination.format_options),
            json.dumps(destination.capacity),
            int(destination.supports_delete),
            destination.delete_strategy,
            destination.timeout_seconds,
            destination.retry.model_dump_json(),
            destination.created_at.isoformat(),
            destination.updated_at.isoformat(),
        ),
    )
    conn.commit()


def get_destination(conn: sqlite3.Connection, destination_id: str) -> Optional[Destination]:
    row = conn.execute(
        f"SELECT {_COLUMNS} FROM destinations WHERE destination_id = ?", (destination_id,)
    ).fetchone()
    return _row_to_destination(row) if row else None


def list_destinations(
    conn: sqlite3.Connection, *, enabled: Optional[bool] = None, paused: Optional[bool] = None
) -> list[Destination]:
    clauses, params = [], []
    if enabled is not None:
        clauses.append("enabled = ?")
        params.append(int(enabled))
    if paused is not None:
        clauses.append("paused = ?")
        params.append(int(paused))
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(f"SELECT {_COLUMNS} FROM destinations {where}", params).fetchall()
    return [_row_to_destination(row) for row in rows]


def set_paused(conn: sqlite3.Connection, destination_id: str, paused: bool) -> Optional[Destination]:
    # Pausar/reanudar pasa por el mismo upsert que cualquier otra edicion
    # (en vez de un UPDATE dedicado) para que `updated_at` y la logica de
    # persistencia se mantengan en un solo camino de codigo.
    destination = get_destination(conn, destination_id)
    if destination is None:
        return None
    destination = destination.model_copy(update={"paused": paused, "updated_at": datetime.now(timezone.utc)})
    upsert_destination(conn, destination)
    return destination


def delete_destination(conn: sqlite3.Connection, destination_id: str) -> None:
    conn.execute("DELETE FROM destinations WHERE destination_id = ?", (destination_id,))
    conn.commit()
