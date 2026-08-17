"""Configuracion de conexion a OpenCTI (URL, TLS, stream_id), editable en
caliente desde el Admin API/UI -- reemplaza las variables de entorno
OPENCTI_* que antes exigian redeploy para cambiar. Mismo patron que
`hub/ingestion_control.py`: una fila por `source_id`, "sin fila todavia" es
un estado valido (Hub sin configurar todavia, no un error) en vez de
forzar una fila de configuracion previa para poder arrancar.

El token NUNCA se guarda en esta tabla: se persiste cifrado en
`hub/secrets_store.py` (mismo mecanismo que credenciales de destinos/
alertas) bajo el nombre fijo `TOKEN_SECRET_NAME`, y se resuelve recien al
construir la conexion (`resolve_opencti_connection`).

Autor: Athan Espinoza
"""
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel

from hub.graphql_client import GraphQLClient
from hub.secret_encryption import SecretCipher
from hub.secrets_store import get_secret

# Nombre fijo del secreto que guarda el token de la cuenta de servicio: hay
# una sola conexion a OpenCTI por source_id en este entorno, asi que no hace
# falta que el operador elija un nombre de secreto -- una indireccion menos.
TOKEN_SECRET_NAME = "opencti-service-account-token"


class OpenCTISettings(BaseModel):
    source_id: str
    url: str
    tls_verify: bool = True
    ca_cert_path: Optional[str] = None
    stream_id: Optional[str] = None
    updated_at: datetime


def init_db(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS opencti_settings (
            source_id TEXT PRIMARY KEY,
            url TEXT NOT NULL,
            tls_verify INTEGER NOT NULL DEFAULT 1,
            ca_cert_path TEXT,
            stream_id TEXT,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    return conn


_COLUMNS = "source_id, url, tls_verify, ca_cert_path, stream_id, updated_at"


def _row_to_settings(row) -> OpenCTISettings:
    return OpenCTISettings(
        source_id=row[0], url=row[1], tls_verify=bool(row[2]), ca_cert_path=row[3],
        stream_id=row[4], updated_at=row[5],
    )


def get_opencti_settings(conn: sqlite3.Connection, source_id: str) -> Optional[OpenCTISettings]:
    """`None` si el Hub todavia no fue configurado para este source_id --
    es el estado esperado al primer arranque, no un error."""
    row = conn.execute(f"SELECT {_COLUMNS} FROM opencti_settings WHERE source_id = ?", (source_id,)).fetchone()
    return _row_to_settings(row) if row else None


def upsert_opencti_settings(
    conn: sqlite3.Connection,
    source_id: str,
    *,
    url: str,
    tls_verify: bool = True,
    ca_cert_path: Optional[str] = None,
    stream_id: Optional[str] = None,
    now: Optional[datetime] = None,
) -> OpenCTISettings:
    settings = OpenCTISettings(
        source_id=source_id,
        url=url.rstrip("/"),
        tls_verify=tls_verify,
        ca_cert_path=ca_cert_path,
        stream_id=stream_id,
        updated_at=now or datetime.now(timezone.utc),
    )
    conn.execute(
        f"""
        INSERT INTO opencti_settings ({_COLUMNS})
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(source_id) DO UPDATE SET
            url = excluded.url,
            tls_verify = excluded.tls_verify,
            ca_cert_path = excluded.ca_cert_path,
            stream_id = excluded.stream_id,
            updated_at = excluded.updated_at
        """,
        (
            settings.source_id,
            settings.url,
            int(settings.tls_verify),
            settings.ca_cert_path,
            settings.stream_id,
            settings.updated_at.isoformat(),
        ),
    )
    conn.commit()
    return settings


@dataclass
class OpenCTIConnection:
    # Bundle de los datos crudos (necesarios para el Live Stream, que no pasa
    # por GraphQLClient) junto con un cliente GraphQL ya armado (para
    # backfill/reconciliacion/validate) -- evita reconstruir el cliente en
    # cada llamador y evita exponer atributos privados de GraphQLClient.
    url: str
    token: str
    verify: object  # bool o path de CA, listo para requests(verify=...)
    stream_id: Optional[str]
    client: GraphQLClient


def resolve_opencti_connection(
    settings_conn: sqlite3.Connection,
    source_id: str,
    *,
    secrets_conn: sqlite3.Connection,
    cipher: Optional[SecretCipher],
) -> Optional[OpenCTIConnection]:
    """`None` si todavia no hay URL+token configurados para este source_id
    -- el llamador (hub.service, hub.api) debe quedarse esperando en vez de
    fallar, ver el patron ya usado para `control.paused` en
    hub/service.py::listen_live_stream."""
    settings = get_opencti_settings(settings_conn, source_id)
    if settings is None or cipher is None:
        return None
    token = get_secret(secrets_conn, TOKEN_SECRET_NAME, cipher=cipher)
    if not token:
        return None
    verify = (settings.ca_cert_path or True) if settings.tls_verify else False
    client = GraphQLClient(settings.url, token, verify=verify)
    return OpenCTIConnection(url=settings.url, token=token, verify=verify, stream_id=settings.stream_id, client=client)
