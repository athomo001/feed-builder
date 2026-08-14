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

from hub.adapters.factory import build_adapter, uses_circuit_breaker
from hub.alert_rules import evaluate_cursor_not_advancing, evaluate_opencti_disconnected
from hub.alerting import build_channels, notify_alerts
from hub.alerting_store import init_db as init_alerts_db, list_alerts, resolve_alert, upsert_alert
from hub.backfill import run_backfill
from hub.config import HubConfig, load_config
from hub.cursor_store import init_db as init_cursor_db, load_cursor, save_cursor
from hub.destinations_store import init_db as init_destinations_db, list_destinations
from hub.graphql_client import GraphQLClient
from hub.ingestion_control import get_control, init_db as init_ingestion_control_db, clear_reconcile_request, clear_rewind_request
from hub.ledger import init_db as init_ledger_db, list_seen_stix_ids
from hub.pipeline import DedupState, process_envelope
from hub.policy_store import init_db as init_policies_db
from hub.reconcile import run_reconciliation
from hub.retry import CircuitBreaker
from hub.sse import iter_sse_events
from hub.taxii_store import init_db as init_taxii_db

_ALERT_EVAL_INTERVAL_SECONDS = 60

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
        self.ingestion_control_conn = init_ingestion_control_db(os.path.join(config.state_dir, "ingestion_control.sqlite3"))
        self.taxii_conn = init_taxii_db(os.path.join(config.state_dir, "taxii.sqlite3"))
        self.alerts_conn = init_alerts_db(os.path.join(config.state_dir, "alerts.sqlite3"))
        self.dedup_state = DedupState()
        self.seen_stix_ids = list_seen_stix_ids(self.ledger_conn)
        # Estado del circuit breaker vive en el proceso, por destino, y
        # persiste entre llamadas a process() (Entrega 2 "circuit breaker
        # por destino", hub/retry.py).
        self.circuit_breakers: dict = {}

    def _build_adapters(self, destinations) -> dict:
        adapters = {}
        for destination in destinations:
            adapters[destination.destination_id] = build_adapter(
                destination, txt_feed_dir=self.config.txt_feed_dir, taxii_conn=self.taxii_conn
            )
            if uses_circuit_breaker(destination):
                self.circuit_breakers.setdefault(destination.destination_id, CircuitBreaker())
        return adapters

    def evaluate_alerts(self) -> None:
        """spec/09 Entrega 4 "Alertas email/webhook": solo las 2 condiciones
        que dependen de estado in-process de ESTE loop (OpenCTI/cursor). El
        resto (dead-letter, destino sin entrega, feed sin rebuild) se
        evaluan desde `POST /admin/api/v1/alerts/evaluate` (proceso del
        Admin API, que ya tiene ledger/destinos/feeds a mano); no hay IPC
        entre `hub.service` y `hub.api` mas alla del SQLite compartido."""
        heartbeat_age = heartbeat_age_seconds(_heartbeat_path(self.config))
        cursor = load_cursor(self.cursor_conn, self.config.source_id)
        cursor_age = (datetime.now(timezone.utc) - cursor.updated_at).total_seconds() if cursor else None

        candidates_by_condition = {
            ("opencti_disconnected", "ingestion"): evaluate_opencti_disconnected(
                heartbeat_age, max_age_seconds=self.config.alert_opencti_disconnected_seconds
            ),
            ("cursor_not_advancing", "ingestion"): evaluate_cursor_not_advancing(
                cursor_age, max_unchanged_seconds=self.config.alert_cursor_stale_seconds
            ),
        }
        active_alerts = []
        for (condition, component), group in candidates_by_condition.items():
            active_resource_ids = {c.resource_id for c in group}
            for candidate in group:
                active_alerts.append(
                    upsert_alert(
                        self.alerts_conn,
                        condition=candidate.condition,
                        severity=candidate.severity,
                        component=candidate.component,
                        resource_id=candidate.resource_id,
                        observed_value=candidate.observed_value,
                    )
                )
            for existing in list_alerts(self.alerts_conn, condition=condition, component=component, state="firing"):
                if existing.resource_id not in active_resource_ids:
                    resolve_alert(self.alerts_conn, existing.alert_id)

        notify_alerts(
            self.alerts_conn, active_alerts, build_channels(self.config), cooldown_seconds=self.config.alert_cooldown_seconds
        )

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


_PAUSE_POLL_INTERVAL_SECONDS = 5


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
    next_alert_eval_ts = time.time() + _ALERT_EVAL_INTERVAL_SECONDS

    while not shutdown_requested():
        # spec/07 "OpenCTI / Ingesta": pausar/reanudar y rebobinar cursor se
        # piden desde el Admin API (proceso separado) via
        # hub/ingestion_control.py; este loop es quien los aplica.
        control = get_control(runtime.ingestion_control_conn, config.source_id)
        if control.paused:
            write_heartbeat(heartbeat_path)  # el proceso sigue vivo, solo no ingesta
            if time.time() >= next_alert_eval_ts:
                runtime.evaluate_alerts()
                next_alert_eval_ts = time.time() + _ALERT_EVAL_INTERVAL_SECONDS
            time.sleep(_PAUSE_POLL_INTERVAL_SECONDS)
            continue

        if control.rewind_to_cursor is not None:
            _log(f"Rewind solicitado: cursor -> {control.rewind_to_cursor!r} (motivo: {control.rewind_reason})")
            last_event_id = control.rewind_to_cursor
            save_cursor(runtime.cursor_conn, config.source_id, last_event_id)
            clear_rewind_request(runtime.ingestion_control_conn, config.source_id)

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
                    control = get_control(runtime.ingestion_control_conn, config.source_id)
                    if now_ts >= next_reconcile_ts or control.reconcile_requested:
                        run_reconciliation_phase(runtime)
                        next_reconcile_ts = now_ts + config.reconcile_interval_seconds
                        if control.reconcile_requested:
                            clear_reconcile_request(runtime.ingestion_control_conn, config.source_id)

                    if now_ts >= next_alert_eval_ts:
                        runtime.evaluate_alerts()
                        next_alert_eval_ts = now_ts + _ALERT_EVAL_INTERVAL_SECONDS

                    if control.paused or control.rewind_to_cursor is not None:
                        _log("Pausa o rewind solicitados durante el stream: reconectando para aplicarlos")
                        break

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
