"""Persistencia del servidor TAXII 2.1 propio (pensado para que un cliente
TAXII externo, por ejemplo Cisco Threat Intelligence Director, haga poll).
SQLite, mismo estilo que `hub/ledger.py`.

Una coleccion TAXII = un destino con `adapter == "taxii2"`; un objeto = un
indicator STIX 2.1 (`hub/stix_bundle.render_stix_indicator`).

Simplificacion deliberada: una fila por (destino, valor normalizado) en vez
de un manifiesto multi-version por objeto STIX (TAXII 2.1 formalmente
permite varias versiones del mismo id, cada una con su propio `date_added`)
-- suficiente para que un consumidor por polling vea el estado mas reciente
via `added_after`, no hace falta una historia version por version que nadie
esta consumiendo todavia.

Autor: Athan Espinoza
"""
import json
import sqlite3
from datetime import datetime, timezone
from typing import Optional


def init_db(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS taxii_objects (
            destination_id TEXT NOT NULL,
            normalized_value TEXT NOT NULL,
            stix_id TEXT NOT NULL,
            object_json TEXT NOT NULL,
            added_at TEXT NOT NULL,
            PRIMARY KEY (destination_id, normalized_value)
        )
        """
    )
    conn.commit()
    return conn


def upsert_object(conn: sqlite3.Connection, destination_id: str, stix_object: dict) -> None:
    # `added_at` se actualiza incluso en un UPDATE (no solo en el INSERT
    # inicial): es la simplificacion "una fila por valor" del docstring del
    # modulo -- como no se guarda historia version por version, el momento
    # en que este valor cambio por ultima vez es lo mas cercano a un
    # `date_added` que un consumidor por `added_after` puede usar.
    value = stix_object["x_hub_normalized_value"]
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO taxii_objects (destination_id, normalized_value, stix_id, object_json, added_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(destination_id, normalized_value) DO UPDATE SET
            stix_id = excluded.stix_id,
            object_json = excluded.object_json,
            added_at = excluded.added_at
        """,
        (destination_id, value, stix_object["id"], json.dumps(stix_object), now),
    )
    conn.commit()


def list_objects(
    conn: sqlite3.Connection, destination_id: str, *, added_after: Optional[str] = None, limit: Optional[int] = None
) -> tuple[list[dict], bool]:
    # Orden ASC por added_at: un cliente TAXII que pagina por `added_after`
    # espera ver los objetos en el mismo orden estable en cada poll,
    # avanzando el cursor con el added_at del ultimo objeto recibido.
    query = "SELECT object_json FROM taxii_objects WHERE destination_id = ?"
    params: list = [destination_id]
    if added_after:
        query += " AND added_at > ?"
        params.append(added_after)
    query += " ORDER BY added_at ASC"
    rows = conn.execute(query, params).fetchall()
    objects = [json.loads(row[0]) for row in rows]
    more = False
    # El recorte a `limit` se hace en Python despues de traer todas las
    # filas (no con LIMIT en el SQL): asi se sabe si sobraron objetos
    # (`more=True`) sin una segunda query COUNT(*) para decidir si hay
    # mas paginas.
    if limit is not None and len(objects) > limit:
        more = True
        objects = objects[:limit]
    return objects, more


def count_objects(conn: sqlite3.Connection, destination_id: str) -> int:
    row = conn.execute("SELECT COUNT(*) FROM taxii_objects WHERE destination_id = ?", (destination_id,)).fetchone()
    return row[0] if row else 0
