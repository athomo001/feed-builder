"""Puesta al dia de UN destino recien asignado a una politica que YA tenia
datos.

El deduplicado global de `hub.pipeline.process_envelope` (`dedup_state`) es
por evento, no por destino: una vez que un IOC se proceso una vez (para
CUALQUIER destino), queda "visto" para siempre en ese proceso, y un destino
nuevo asignado despues nunca lo recibe -- aunque ESE destino en particular
nunca lo haya visto. Bug real reportado por el operador (2026-08-18): un
destino Wazuh asignado a una politica que ya alimentaba un TXT hacia horas
quedo pegado en 1 entrada por subtipo (solo lo que llego en vivo despues de
asignarse).

Este modulo reusa el backfill normal (`hub.backfill.run_backfill`/
`run_backfill_observables`) -- misma paginacion, misma ventana temporal,
mismo filtro de tipos soportados -- pero con un `on_envelope` que evalua
SOLO contra el destino nuevo (`hub.pipeline.process_envelope_for_destination`,
que deliberadamente no consulta el deduplicado global) en vez del
`runtime.process` multi-destino de `hub.service`.

Autor: Athan Espinoza
"""
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from hub.backfill import BackfillResult, run_backfill, run_backfill_observables
from hub.destinations_store import Destination
from hub.graphql_client import GraphQLClient
from hub.pipeline import process_envelope_for_destination


@dataclass
class ResyncResult:
    indicators: BackfillResult
    observables: BackfillResult


def resync_destination(
    client: GraphQLClient,
    *,
    destination: Destination,
    source_id: str,
    since: datetime,
    max_pages: int,
    page_size: int,
    policies_conn,
    adapters: dict,
    ledger_conn,
    circuit_breakers: dict,
    default_ttl_days: int,
    delivery_queue_conn=None,
    should_stop: Callable[[], bool] = lambda: False,
) -> ResyncResult:
    def on_envelope(envelope: dict) -> None:
        process_envelope_for_destination(
            envelope,
            event_id=str(uuid.uuid4()),
            source_id=source_id,
            destination=destination,
            policies_conn=policies_conn,
            adapters=adapters,
            ledger_conn=ledger_conn,
            circuit_breakers=circuit_breakers,
            default_ttl_days=default_ttl_days,
            delivery_queue_conn=delivery_queue_conn,
        )

    indicators = run_backfill(
        client, since=since, max_pages=max_pages, page_size=page_size, on_envelope=on_envelope, should_stop=should_stop,
    )
    observables = run_backfill_observables(
        client, since=since, max_pages=max_pages, page_size=page_size, on_envelope=on_envelope, should_stop=should_stop,
    )
    return ResyncResult(indicators=indicators, observables=observables)
