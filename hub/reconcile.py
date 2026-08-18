"""Reconciliacion periodica via GraphQL: detecta eventos faltantes, cursor
detenido o divergencia local comparando lo que OpenCTI reporta tener contra
lo que el ledger local ya proceso.

`find_gaps` es deliberadamente una funcion pura: compara lo que GraphQL dice
que existe contra lo que el ledger local ya vio, sin decidir por si sola que
hacer con la brecha. `run_reconciliation` es la orquestacion minima: si hay
brecha, reprocesa la ventana via `run_backfill` -- pausando la confirmacion
del cursor mientras tanto es responsabilidad del llamador; el ledger/dedup
existente es lo que evita duplicar al reprocesar.

Autor: Athan Espinoza
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Iterable

from hub.backfill import BackfillResult, run_backfill
from hub.graphql_client import GraphQLClient, extract_nodes
from hub.graphql_indicator import BACKFILL_ACTIVE_ONLY_FILTERS, BACKFILL_INDICATORS_QUERY


def find_gaps(
    client: GraphQLClient, *, since: datetime, seen_stix_ids: Iterable[str], page_size: int = 100, max_pages: int = 10,
) -> list[str]:
    """Devuelve los `stix_id` que GraphQL reporta modificados desde `since`
    pero que no estan en `seen_stix_ids` (lo que el ledger local ya proceso)."""
    seen = set(seen_stix_ids)
    gaps: list[str] = []
    cursor = None

    # Mismo filtro que el backfill (revoked=false + tipos con adaptador,
    # BACKFILL_ACTIVE_ONLY_FILTERS): sin esto, esta consulta contaba como
    # "brecha" cualquier indicador revocado o de un tipo sin adaptador
    # (Artifact, Text...) -- esos nunca entran a `seen_stix_ids` porque
    # backfill/Live Stream los descartan a proposito, asi que aparecian como
    # gap en TODAS las reconciliaciones, para siempre, sin que hubiera nada
    # que reparar. Confirmado con un caso real: 44166 "gaps" reportados de
    # una sola pasada, la enorme mayoria de tipos sin adaptador.
    #
    # `max_pages` (igual que `run_backfill`) acota cuanto se puede tardar
    # esta funcion: sin tope, un catalogo grande con divergencia real
    # paginaba sin limite (potencialmente cientos de paginas, cada una un
    # roundtrip de red) y bloqueaba todo `listen_live_stream` -- heartbeat
    # sin escribirse, sin reconectar el Live Stream -- mientras tanto.
    pages = 0
    while pages < max(1, max_pages):
        data = client.query(
            BACKFILL_INDICATORS_QUERY,
            {
                "first": max(1, page_size), "after": cursor, "orderBy": "modified", "orderMode": "desc",
                "filters": BACKFILL_ACTIVE_ONLY_FILTERS,
            },
        )
        conn = data.get("indicators") or {}
        nodes, end_cursor, has_next = extract_nodes(conn)
        if not nodes:
            break
        pages += 1

        window_reached = False
        for node in nodes:
            modified_raw = node.get("modified") or node.get("created")
            if modified_raw:
                modified = datetime.fromisoformat(str(modified_raw).replace("Z", "+00:00"))
                if modified < since:
                    window_reached = True
                    continue
            if node.get("id") not in seen:
                gaps.append(node["id"])

        if window_reached or not has_next or not end_cursor:
            break
        cursor = end_cursor

    return gaps


@dataclass
class ReconciliationReport:
    gaps_found: list[str]
    backfill_result: "BackfillResult | None" = None


def run_reconciliation(
    client: GraphQLClient,
    *,
    since: datetime,
    seen_stix_ids: Iterable[str],
    on_envelope: Callable[[dict], None],
    max_pages: int = 5,
    page_size: int = 100,
) -> ReconciliationReport:
    gaps = find_gaps(client, since=since, seen_stix_ids=seen_stix_ids, page_size=page_size, max_pages=max_pages)
    if not gaps:
        return ReconciliationReport(gaps_found=gaps)

    backfill_result = run_backfill(
        client,
        since=since,
        max_pages=max_pages,
        page_size=page_size,
        on_envelope=on_envelope,
    )
    return ReconciliationReport(gaps_found=gaps, backfill_result=backfill_result)
