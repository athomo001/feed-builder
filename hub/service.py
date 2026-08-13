"""Orquestacion del flujo operativo (spec/02-OPENCTI-COMPATIBILITY.md "Flujo
operativo"): validar conexion -> backfill acotado -> abrir Live Stream ->
normalizar -> politicas -> persistir -> reconciliacion periodica -> apagado
cooperativo.

Puerto del loop principal de opencti_feed_builder.py (backoff/reconexion,
apagado cooperativo con SIGTERM/SIGINT, heartbeat) sobre los modulos nuevos
de Entrega 1 (hub.sse, hub.graphql_client, hub.backfill, hub.reconcile,
hub.pipeline, hub.txt_feed), en vez de la logica de extraccion ad-hoc del
script legado.
"""
import json
import os
import signal
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests

from hub.adapters.http_push_adapter import HttpPushAdapter
from hub.adapters.txt_feed_adapter import TxtFeedAdapter
from hub.backfill import run_backfill
from hub.config import HubConfig, load_config
from hub.cursor_store import init_db as init_cursor_db, load_cursor, save_cursor
from hub.destinations_store import init_db as init_destinations_db, list_destinations
from hub.graphql_client import GraphQLClient
from hub.ledger import init_db as init_ledger_db, list_seen_stix_ids
from hub.pipeline import DedupState, process_envelope
from hub.policy_store import init_db as init_policies_db
from hub.reconcile import run_reconciliation
from hub.retry import CircuitBreaker
from hub.sse import iter_sse_events

_PING_QUERY = "query HubPing { indicators(first: 1) { pageInfo { hasNextPage } } }"


def _log(msg: str) -> None:
    print(f"[hub] {msg}", flush=True)


# --- Apagado cooperativo (spec/09 Entrega 1 "graceful shutdown") ----------

_shutdown_requested = False


def request_shutdown(signum=None, frame=None) -> None:
    global _shutdown_requested
    _shutdown_requested = True


def shutdown_requested() -> bool:
    return _shutdown_requested


def reset_shutdown_for_tests() -> None:
    global _shutdown_requested
    _shutdown_requested = False


# --- Heartbeat / healthcheck ------------------------------------------------


def _heartbeat_path(config: HubConfig) -> str:
    return os.path.join(config.state_dir, ".heartbeat")


def write_heartbeat(path: str) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(repr(time.time()))
    os.replace(tmp, path)


def heartbeat_age_seconds(path: str) -> Optional[float]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            ts = float(f.read().strip())
        return time.time() - ts
    except Exception:
        return None


def is_healthy(path: str, max_age_seconds: int = 600) -> bool:
    age = heartbeat_age_seconds(path)
    return age is not None and age <= max_age_seconds


# --- Conexion ---------------------------------------------------------------


def stream_url(config: HubConfig) -> str:
    if config.opencti_stream_id:
        return f"{config.opencti_url}/stream/{config.opencti_stream_id}"
    return f"{config.opencti_url}/stream"


def validate_connection(client: GraphQLClient) -> None:
    """spec/02 flujo de entrada paso 1: 'Validar URL, TLS y token'."""
    client.query(_PING_QUERY)


class HubRuntime:
    def __init__(self, config: HubConfig):
        self.config = config
        self.client = GraphQLClient(config.opencti_url, config.opencti_token, verify=config.verify)
        os.makedirs(config.state_dir, exist_ok=True)
        self.cursor_conn = init_cursor_db(os.path.join(config.state_dir, "cursor.sqlite3"))
        self.ledger_conn = init_ledger_db(os.path.join(config.state_dir, "ledger.sqlite3"))
        self.destinations_conn = init_destinations_db(os.path.join(config.state_dir, "destinations.sqlite3"))
        self.policies_conn = init_policies_db(os.path.join(config.state_dir, "policies.sqlite3"))
        self.dedup_state = DedupState()
        self.seen_stix_ids = list_seen_stix_ids(self.ledger_conn)
        # Estado del circuit breaker vive en el proceso, por destino, y
        # persiste entre llamadas a process() (Entrega 2 "circuit breaker
        # por destino", hub/retry.py).
        self.circuit_breakers: dict = {}

    def _build_adapters(self, destinations) -> dict:
        adapters = {}
        for destination in destinations:
            if destination.adapter == "txt_feed":
                adapters[destination.destination_id] = TxtFeedAdapter(destination, base_dir=self.config.txt_feed_dir)
            elif destination.adapter == "http_push":
                adapters[destination.destination_id] = HttpPushAdapter(destination)
                self.circuit_breakers.setdefault(destination.destination_id, CircuitBreaker())
        return adapters

    def process(self, envelope: dict):
        destinations = list_destinations(self.destinations_conn, enabled=True, paused=False)
        adapters = self._build_adapters(destinations)
        result = process_envelope(
            envelope,
            event_id=str(uuid.uuid4()),
            source_id=self.config.source_id,
            dedup_state=self.dedup_state,
            destinations=destinations,
            policies_conn=self.policies_conn,
            adapters=adapters,
            ledger_conn=self.ledger_conn,
            circuit_breakers=self.circuit_breakers,
            default_ttl_days=self.config.policy_ttl_days,
        )
        self.seen_stix_ids.add(result.event.stix_id)
        return result


# --- Backfill ----------------------------------------------------------------


def run_backfill_phase(runtime: HubRuntime):
    config = runtime.config
    since = datetime.now(timezone.utc) - timedelta(days=config.backfill_window_days)
    result = run_backfill(
        runtime.client,
        since=since,
        max_pages=config.backfill_max_pages,
        page_size=config.backfill_page_size,
        on_envelope=runtime.process,
        should_stop=shutdown_requested,
    )
    _log(
        f"Backfill done: pages={result.pages} indicators={result.indicators_seen} "
        f"envelopes={result.envelopes_emitted} reason={result.stopped_reason}"
    )
    return result


# --- Reconciliacion ------------------------------------------------------


def run_reconciliation_phase(runtime: HubRuntime):
    config = runtime.config
    since = datetime.now(timezone.utc) - timedelta(days=config.backfill_window_days)
    report = run_reconciliation(
        runtime.client,
        since=since,
        seen_stix_ids=runtime.seen_stix_ids,
        on_envelope=runtime.process,
        max_pages=config.backfill_max_pages,
        page_size=config.backfill_page_size,
    )
    if report.gaps_found:
        _log(f"RECONCILE: gaps_found={len(report.gaps_found)}")
    return report


# --- Live stream ---------------------------------------------------------


def listen_live_stream(runtime: HubRuntime, *, session=None):
    config = runtime.config
    session = session or requests
    headers = {
        "Authorization": f"Bearer {config.opencti_token}",
        "Accept": "text/event-stream",
    }

    saved_cursor = load_cursor(runtime.cursor_conn, config.source_id)
    last_event_id = saved_cursor.cursor_value if saved_cursor else None

    backoff = 2
    heartbeat_path = _heartbeat_path(config)
    next_reconcile_ts = time.time() + config.reconcile_interval_seconds

    while not shutdown_requested():
        request_headers = dict(headers)
        if last_event_id:
            request_headers["Last-Event-ID"] = last_event_id

        url = stream_url(config)
        _log(f"Connecting SSE: {url}")
        try:
            with session.get(url, headers=request_headers, stream=True, timeout=60, verify=config.verify) as r:
                if r.status_code == 401:
                    raise RuntimeError("401 Unauthorized (token invalido?)")
                r.raise_for_status()
                backoff = 2

                for sse_event in iter_sse_events(
                    r.iter_lines(),
                    max_line_bytes=config.max_sse_line_bytes,
                    max_event_bytes=config.max_sse_event_bytes,
                ):
                    try:
                        envelope = json.loads(sse_event.data.decode("utf-8"))
                        runtime.process(envelope)
                    except Exception as e:
                        _log(f"EVENT_PROCESS_ERROR: {e}")

                    if sse_event.id:
                        last_event_id = sse_event.id
                        save_cursor(runtime.cursor_conn, config.source_id, last_event_id)

                    write_heartbeat(heartbeat_path)

                    now_ts = time.time()
                    if now_ts >= next_reconcile_ts:
                        run_reconciliation_phase(runtime)
                        next_reconcile_ts = now_ts + config.reconcile_interval_seconds

                    if shutdown_requested():
                        _log("Shutdown requested, closing stream cooperatively")
                        break
        except Exception as e:
            _log(f"ERROR: {e} (reconnect in {backoff}s)")
            if shutdown_requested():
                break
            time.sleep(backoff)
            backoff = min(backoff * 2, 60)


# --- Entrypoint ------------------------------------------------------------


def run(config: HubConfig) -> None:
    signal.signal(signal.SIGTERM, request_shutdown)
    signal.signal(signal.SIGINT, request_shutdown)

    runtime = HubRuntime(config)
    write_heartbeat(_heartbeat_path(config))

    _log("Validating OpenCTI connection")
    validate_connection(runtime.client)

    run_backfill_phase(runtime)
    listen_live_stream(runtime)

    _log("Shutdown complete")


def main() -> None:
    config = load_config()
    if "--healthcheck" in sys.argv:
        sys.exit(0 if is_healthy(_heartbeat_path(config)) else 1)
    run(config)


if __name__ == "__main__":
    main()
