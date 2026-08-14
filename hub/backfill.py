"""Backfill inicial via GraphQL: recorre el catalogo de indicadores paginando
por cursor, acotado a una ventana temporal, con paginas limitadas y
cancelable a mitad de camino.

Autor: Athan Espinoza
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional

from hub.graphql_client import GraphQLClient, extract_nodes
from hub.graphql_indicator import BACKFILL_INDICATORS_QUERY, indicator_node_to_envelope


def _log(msg: str) -> None:
    print(f"[hub] {msg}", flush=True)


def _parse_dt(value) -> Optional[datetime]:
    if not value:
        return None
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


@dataclass
class BackfillResult:
    pages: int = 0
    indicators_seen: int = 0
    envelopes_emitted: int = 0
    skipped_errors: int = 0
    last_cursor: Optional[str] = None
    stopped_reason: str = "exhausted"  # exhausted | max_pages | should_stop | window


def run_backfill(
    client: GraphQLClient,
    *,
    since: datetime,
    max_pages: int,
    page_size: int,
    on_envelope: Callable[[dict], None],
    should_stop: Callable[[], bool] = lambda: False,
) -> BackfillResult:
    """Recorre `indicators` ordenado por modified desc, mas nuevo primero, y
    corta cuando: se agotan paginas, se llega a `since`, `should_stop()` es
    True, o se alcanza `max_pages`. Ordenar por mas reciente primero permite
    cortar apenas se cruza la ventana `since` sin recorrer el catalogo
    completo."""
    result = BackfillResult()
    cursor = None

    while result.pages < max(1, max_pages):
        if should_stop():
            result.stopped_reason = "should_stop"
            break

        data = client.query(
            BACKFILL_INDICATORS_QUERY,
            {"first": max(1, page_size), "after": cursor, "orderBy": "modified", "orderMode": "desc"},
        )
        conn = data.get("indicators") or {}
        nodes, end_cursor, has_next = extract_nodes(conn)
        if not nodes:
            result.stopped_reason = "exhausted"
            break

        result.pages += 1
        result.indicators_seen += len(nodes)

        window_reached = False
        for node in nodes:
            modified = _parse_dt(node.get("modified") or node.get("created"))
            if modified is not None and modified < since:
                window_reached = True
                continue
            try:
                # Un indicador individual no clasificable o malformado (ej.
                # main_observable_type sin adaptador, como "Artifact") no debe
                # tumbar todo el backfill -- mismo criterio que ya aplica el
                # Live Stream en hub/service.py alrededor de runtime.process.
                on_envelope(indicator_node_to_envelope(node, action="create"))
                result.envelopes_emitted += 1
            except Exception as e:
                result.skipped_errors += 1
                _log(f"BACKFILL_NODE_ERROR: id={node.get('id')!r}: {e}")

        if window_reached:
            result.stopped_reason = "window"
            break

        if not has_next or not end_cursor:
            result.stopped_reason = "exhausted"
            break

        cursor = end_cursor
        result.last_cursor = cursor
    else:
        # El `else` de este `while` solo corre si el ciclo termina porque la
        # condicion se volvio falsa (se agoto `max_pages`), no si termino por
        # un `break` en otra rama: es la forma idiomatica de Python de
        # distinguir "se acabaron las paginas permitidas" de las demas salidas.
        result.stopped_reason = "max_pages"

    return result
