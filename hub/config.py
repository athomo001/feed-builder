"""Configuracion propia del Hub (spec/02-OPENCTI-COMPATIBILITY.md "Configuracion
minima de conexion", spec/03-ARCHITECTURE.md "Hub tiene su propio archivo de
configuracion/secretos, separado del de OpenCTI").

Solo lee variables con prefijo propio del Hub; nunca las compartidas del
.env de OpenCTI (MinIO, RabbitMQ, Neo4j, conectores, etc. - ver spec/02).
"""
import os
from dataclasses import dataclass
from typing import Optional


@dataclass
class HubConfig:
    opencti_url: str
    opencti_token: str
    opencti_stream_id: Optional[str] = None
    tls_verify: bool = True
    ca_cert_path: Optional[str] = None

    policy_ttl_days: int = 30

    backfill_max_pages: int = 10
    backfill_page_size: int = 100
    backfill_window_days: int = 7

    reconcile_interval_seconds: int = 600

    max_sse_line_bytes: int = 256 * 1024
    max_sse_event_bytes: int = 2 * 1024 * 1024

    txt_feed_dir: str = "./feeds"
    txt_feed_max_records: int = 20000

    source_id: str = "opencti-main"
    state_dir: str = "./state"

    @property
    def verify(self):
        """Valor listo para requests(verify=...): CA cert path, bool o True."""
        if not self.tls_verify:
            return False
        return self.ca_cert_path or True


def load_config(env: Optional[dict] = None) -> HubConfig:
    e = env if env is not None else os.environ

    def get(name, default=None):
        return e.get(name, default)

    url = get("OPENCTI_URL")
    token = get("OPENCTI_SERVICE_ACCOUNT_TOKEN")
    if not url:
        raise RuntimeError("OPENCTI_URL is not set")
    if not token:
        raise RuntimeError("OPENCTI_SERVICE_ACCOUNT_TOKEN is not set")

    def env_bool(name, default):
        value = get(name)
        if value is None:
            return default
        return str(value).strip().lower() in ("1", "true", "yes", "on")

    def env_int(name, default):
        value = get(name)
        if value is None or str(value).strip() == "":
            return default
        return int(value)

    return HubConfig(
        opencti_url=url.rstrip("/"),
        opencti_token=token,
        opencti_stream_id=get("OPENCTI_STREAM_ID") or None,
        tls_verify=env_bool("OPENCTI_TLS_VERIFY", True),
        ca_cert_path=get("OPENCTI_CA_CERT_PATH") or None,
        policy_ttl_days=env_int("POLICY_TTL_DAYS", 30),
        backfill_max_pages=env_int("BACKFILL_MAX_PAGES", 10),
        backfill_page_size=env_int("BACKFILL_PAGE_SIZE", 100),
        backfill_window_days=env_int("BACKFILL_WINDOW_DAYS", 7),
        reconcile_interval_seconds=env_int("RECONCILE_INTERVAL_SECONDS", 600),
        max_sse_line_bytes=env_int("MAX_SSE_LINE_BYTES", 256 * 1024),
        max_sse_event_bytes=env_int("MAX_SSE_EVENT_BYTES", 2 * 1024 * 1024),
        txt_feed_dir=get("TXT_FEED_DIR", "./feeds"),
        txt_feed_max_records=env_int("TXT_FEED_MAX_RECORDS", 20000),
        source_id=get("HUB_SOURCE_ID", "opencti-main"),
        state_dir=get("HUB_STATE_DIR", "./state"),
    )
