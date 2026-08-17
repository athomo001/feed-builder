"""Cliente GraphQL minimo. GraphQL se usa para backfill, reconciliacion,
validacion y metadatos -- nunca como mecanismo principal de polling en
tiempo real (para eso esta el Live Stream via SSE).

Reimplementacion de `post_graphql`/`extract_nodes` de un script legado
(opencti_feed_builder.py), parametrizada con la configuracion propia del
Hub (hub/config.py) en vez de leer variables de entorno directamente, y con
TLS verify/CA cert explicitos para poder validar contra un CA propio sin
tener que desactivar la verificacion de certificado.

Autor: Athan Espinoza
"""
from typing import Optional

import requests


class GraphQLError(RuntimeError):
    pass


# Query minima y barata: solo sirve para confirmar que la URL, el TLS y el
# token son validos, sin traer datos reales. Compartida entre hub.service
# (validar antes de arrancar backfill/stream) y el router
# opencti_settings.test (botón "probar conexion" de la UI).
PING_QUERY = "query HubPing { indicators(first: 1) { pageInfo { hasNextPage } } }"


class GraphQLClient:
    def __init__(self, url: str, token: str, *, verify=True, timeout_seconds: int = 120):
        self._graphql_url = url.rstrip("/") + "/graphql"
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        self._verify = verify
        self._timeout = timeout_seconds

    def query(self, query: str, variables: Optional[dict] = None) -> dict:
        resp = requests.post(
            self._graphql_url,
            headers=self._headers,
            json={"query": query, "variables": variables or {}},
            timeout=self._timeout,
            verify=self._verify,
        )
        resp.raise_for_status()
        data = resp.json()
        # GraphQL puede responder 200 OK con un array "errors" en el body:
        # ese caso tambien es un fallo y debe propagarse, no solo el status HTTP.
        if isinstance(data, dict) and data.get("errors"):
            raise GraphQLError(f"GraphQL errors: {data.get('errors')}")
        return (data or {}).get("data") or {}


def extract_nodes(connection_obj: Optional[dict]):
    """Convierte una respuesta GraphQL con edges/pageInfo a (nodes, end_cursor, has_next)."""
    if not isinstance(connection_obj, dict):
        return [], None, False

    edges = connection_obj.get("edges") or []
    nodes = []
    if isinstance(edges, list):
        for edge in edges:
            if isinstance(edge, dict) and isinstance(edge.get("node"), dict):
                nodes.append(edge["node"])

    page = connection_obj.get("pageInfo") or {}
    end_cursor = page.get("endCursor")
    has_next = bool(page.get("hasNextPage"))
    return nodes, end_cursor, has_next
