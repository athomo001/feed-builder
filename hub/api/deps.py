"""Estado compartido del Admin API (spec/03-ARCHITECTURE.md "Admin API:
servicio separado del consumidor OpenCTI"). Todas las conexiones SQLite y
el `GraphQLClient` se crean una vez en `app.state.hub` y los routers los
leen desde ahi -- mismo patron de conexiones reusadas que `hub/service.py`.
"""
from dataclasses import dataclass
from typing import Optional

from fastapi import Request

from hub.config import HubConfig
from hub.graphql_client import GraphQLClient


@dataclass
class APIState:
    config: HubConfig
    destinations_conn: object
    policies_conn: object
    ledger_conn: object
    tokens_conn: object
    idempotency_conn: object
    graphql_client: Optional[GraphQLClient] = None
    circuit_breakers: dict = None

    def __post_init__(self):
        if self.circuit_breakers is None:
            self.circuit_breakers = {}


def get_state(request: Request) -> APIState:
    return request.app.state.hub
