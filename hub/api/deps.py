"""Estado compartido del Admin API, que corre como servicio separado del
consumidor de OpenCTI. Todas las conexiones SQLite y el `GraphQLClient` se
crean una vez en `app.state.hub` y los routers los leen desde ahi -- mismo
patron de conexiones reusadas que `hub/service.py`, para no abrir una
conexion nueva por request.

Autor: Athan Espinoza
"""
from dataclasses import dataclass
from typing import Optional

from fastapi import Request

from hub.config import HubConfig
from hub.graphql_client import GraphQLClient


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
    taxii_conn: object = None
    alerts_conn: object = None
    secrets_conn: object = None
    secret_cipher: object = None
    oidc_sessions_conn: object = None
    graphql_client: Optional[GraphQLClient] = None
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
