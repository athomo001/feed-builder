"""Backfill inicial via GraphQL (spec/02-OPENCTI-COMPATIBILITY.md "Backfill":
paginacion por cursor, ventana temporal, paginas limitadas, cancelable).
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional

from hub.graphql_client import GraphQLClient, extract_nodes
from hub.graphql_indicator import BACKFILL_INDICATORS_QUERY, indicator_node_to_envelope


def _parse_dt(value) -> Optional[datetime]:
    if not value:
        return None
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


@dataclass
class BackfillResult:
    pages: int = 0
    indicators_seen: int = 0
    envelopes_emitted: int = 0
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
    True, o se alcanza `max_pages` (spec/02 "Limitar paginas, tamano de
    pagina y ventana temporal", "Permitir cancelar o pausar")."""
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
            on_envelope(indicator_node_to_envelope(node, action="create"))
            result.envelopes_emitted += 1

        if window_reached:
            result.stopped_reason = "window"
            break

        if not has_next or not end_cursor:
            result.stopped_reason = "exhausted"
            break

        cursor = end_cursor
        result.last_cursor = cursor
    else:
        result.stopped_reason = "max_pages"

    return result
