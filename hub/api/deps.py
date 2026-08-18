"""Estado compartido del Admin API, que corre como servicio separado del
consumidor de OpenCTI. Todas las conexiones SQLite se crean una vez en
`app.state.hub` y los routers las leen desde ahi -- mismo patron de
conexiones reusadas que `hub/service.py`, para no abrir una conexion nueva
por request.

El `GraphQLClient` es la excepcion: NO se cachea en `APIState` (a
diferencia de las conexiones SQLite) porque depende de la configuracion de
OpenCTI guardada por el operador via `opencti_settings`/`secrets`, que
puede cambiar en caliente -- `get_graphql_client()` lo resuelve fresco en
cada request en vez de arriesgarse a servir un cliente con URL/token viejos.

Autor: Athan Espinoza
"""
from dataclasses import dataclass
from typing import Optional

from fastapi import Depends, Request

from hub.config import HubConfig
from hub.graphql_client import GraphQLClient
from hub.opencti_settings_store import resolve_opencti_connection


@dataclass
class APIState:
    # Campos con default=None son las conexiones/features que solo existen
    # si el destino/feature correspondiente esta habilitado (taxii, alerts,
    # secrets, oidc): mantenerlos opcionales evita forzar a crear esas
    # conexiones en despliegues que no usan esa funcionalidad.
    config: HubConfig
    destinations_conn: object
    policies_conn: object
    ledger_conn: object
    tokens_conn: object
    idempotency_conn: object
    audit_conn: object
    ingestion_control_conn: object
    cursor_conn: object
    delivery_queue_conn: object
    opencti_settings_conn: object = None
    taxii_conn: object = None
    alerts_conn: object = None
    secrets_conn: object = None
    secret_cipher: object = None
    oidc_sessions_conn: object = None
    circuit_breakers: dict = None

    def __post_init__(self):
        # circuit_breakers no se puede declarar con un dict literal como
        # default (mutable compartido entre instancias): se inicializa aca
        # para que cada APIState tenga su propio dict, uno por destino,
        # que persiste durante toda la vida del proceso.
        if self.circuit_breakers is None:
            self.circuit_breakers = {}


def get_state(request: Request) -> APIState:
    # Unico punto de acceso al estado compartido: los routers dependen de
    # esta funcion via Depends() en vez de importar `app` directamente, asi
    # se puede mockear en tests sin levantar una app FastAPI real.
    return request.app.state.hub


def get_graphql_client(state: APIState = Depends(get_state)) -> Optional[GraphQLClient]:
    """`None` si OpenCTI todavia no fue configurado (ver
    `hub/api/routers/opencti_settings.py`) -- el router que lo use decide
    como reportarlo (los existentes devuelven 409 `opencti_not_configured`,
    ver `hub/api/routers/deliveries.py`/`policies.py`)."""
    connection = resolve_opencti_connection(
        state.opencti_settings_conn, state.config.source_id,
        secrets_conn=state.secrets_conn, cipher=state.secret_cipher,
    )
    return connection.client if connection else None
