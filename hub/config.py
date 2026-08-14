"""Configuracion propia del Hub, separada del archivo de configuracion/
secretos de OpenCTI.

Solo lee variables con prefijo propio del Hub; nunca las compartidas del
.env de OpenCTI (MinIO, RabbitMQ, Neo4j, conectores, etc.).

Autor: Athan Espinoza
"""
import os
from dataclasses import dataclass
from typing import Optional


@dataclass
class HubConfig:
    # Un campo por variable de entorno relevante para el Hub (ver
    # load_config mas abajo); usar un dataclass en vez de un dict le da
    # tipado y autocompletado al resto del codigo.
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

    # CORS cerrado por defecto (lista vacia); se habilita explicitamente al
    # origen real de la UI Angular via ADMIN_UI_ORIGINS.
    admin_ui_origins: list = None  # type: ignore[assignment]

    # Cooldown para no inundar al operador con alertas repetidas (el valor
    # no viene de ninguna configuracion externa, se fija aca) y umbrales
    # independientes por condicion. `alert_destination_stale_seconds` es un
    # umbral unico a nivel Hub, no un SLO por destino individual.
    alert_cooldown_seconds: int = 300
    alert_opencti_disconnected_seconds: int = 120
    alert_cursor_stale_seconds: int = 300
    alert_destination_stale_seconds: int = 3600
    alert_feed_stale_seconds: int = 1800
    alert_smtp_host: Optional[str] = None
    alert_smtp_port: int = 25
    alert_email_from: Optional[str] = None
    alert_email_to: list = None  # type: ignore[assignment]
    alert_email_credential_ref: Optional[str] = None
    alert_email_min_severity: str = "warning"
    alert_webhook_url: Optional[str] = None
    alert_webhook_credential_ref: Optional[str] = None
    alert_webhook_min_severity: str = "info"

    # Cifrado en reposo con clave externa: se eligio este enfoque en vez de
    # integrar un secret manager externo real porque cumple el mismo
    # objetivo de seguridad sin depender de infraestructura adicional. La
    # clave nunca vive en la DB ni en `state_dir`; `secret_encryption_key_file`
    # es una alternativa a pasarla directo por variable de entorno (por
    # ejemplo un secret montado como archivo en el orquestador).
    secret_encryption_key: Optional[str] = None
    secret_encryption_key_file: Optional[str] = None

    # Los API tokens existentes siguen sin cambios para automatizacion; esto
    # es un segundo mecanismo pensado para humanos interactivos, no un
    # reemplazo. Sin IdP real disponible en este entorno para probar contra
    # el (ver hub/oidc_client.py).
    oidc_issuer_url: Optional[str] = None
    oidc_client_id: Optional[str] = None
    oidc_client_secret_ref: Optional[str] = None
    oidc_redirect_uri: Optional[str] = None
    oidc_role_claim: str = "roles"
    oidc_role_mapping: dict = None  # type: ignore[assignment]  # claim-value -> Role
    oidc_session_ttl_seconds: int = 28800

    # OpenTelemetry es una salida opcional: sin `otel_exporter_endpoint`
    # configurado, `hub/tracing.py` no instala nada (el tracer global de
    # OTel ya es no-op por defecto).
    otel_exporter_endpoint: Optional[str] = None
    otel_service_name: str = "opencti-ioc-hub"

    def __post_init__(self):
        # Los defaults mutables (list/dict) no pueden ir directo en la firma
        # del dataclass porque se compartirian entre instancias; se
        # inicializan aca cuando llegan en None.
        if self.admin_ui_origins is None:
            self.admin_ui_origins = []
        if self.alert_email_to is None:
            self.alert_email_to = []
        if self.oidc_role_mapping is None:
            self.oidc_role_mapping = {}

    @property
    def verify(self):
        """Valor listo para requests(verify=...): CA cert path, bool o True."""
        if not self.tls_verify:
            return False
        return self.ca_cert_path or True


def load_config(env: Optional[dict] = None) -> HubConfig:
    # `env` es inyectable (en vez de leer siempre os.environ) para que los
    # tests puedan construir un HubConfig sin mutar variables de entorno
    # reales del proceso.
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
        # Acepta las representaciones textuales mas comunes de "true" que
        # vienen de un .env o de variables de entorno del orquestador.
        value = get(name)
        if value is None:
            return default
        return str(value).strip().lower() in ("1", "true", "yes", "on")

    def env_int(name, default):
        # Trata "" igual que ausente: una variable seteada pero vacia en el
        # .env no debe romper el parseo a int.
        value = get(name)
        if value is None or str(value).strip() == "":
            return default
        return int(value)

    def _json_dict(value):
        if not value:
            return {}
        # Import local: json solo hace falta si OIDC_ROLE_MAPPING viene seteado.
        import json

        return json.loads(value)

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
        admin_ui_origins=[o.strip() for o in (get("ADMIN_UI_ORIGINS") or "").split(",") if o.strip()],
        alert_cooldown_seconds=env_int("ALERT_COOLDOWN_SECONDS", 300),
        alert_opencti_disconnected_seconds=env_int("ALERT_OPENCTI_DISCONNECTED_SECONDS", 120),
        alert_cursor_stale_seconds=env_int("ALERT_CURSOR_STALE_SECONDS", 300),
        alert_destination_stale_seconds=env_int("ALERT_DESTINATION_STALE_SECONDS", 3600),
        alert_feed_stale_seconds=env_int("ALERT_FEED_STALE_SECONDS", 1800),
        alert_smtp_host=get("ALERT_SMTP_HOST") or None,
        alert_smtp_port=env_int("ALERT_SMTP_PORT", 25),
        alert_email_from=get("ALERT_EMAIL_FROM") or None,
        alert_email_to=[o.strip() for o in (get("ALERT_EMAIL_TO") or "").split(",") if o.strip()],
        alert_email_credential_ref=get("ALERT_EMAIL_CREDENTIAL_REF") or None,
        alert_email_min_severity=get("ALERT_EMAIL_MIN_SEVERITY", "warning"),
        alert_webhook_url=get("ALERT_WEBHOOK_URL") or None,
        alert_webhook_credential_ref=get("ALERT_WEBHOOK_CREDENTIAL_REF") or None,
        alert_webhook_min_severity=get("ALERT_WEBHOOK_MIN_SEVERITY", "info"),
        secret_encryption_key=get("SECRET_ENCRYPTION_KEY") or None,
        secret_encryption_key_file=get("SECRET_ENCRYPTION_KEY_FILE") or None,
        oidc_issuer_url=get("OIDC_ISSUER_URL") or None,
        oidc_client_id=get("OIDC_CLIENT_ID") or None,
        oidc_client_secret_ref=get("OIDC_CLIENT_SECRET_REF") or None,
        oidc_redirect_uri=get("OIDC_REDIRECT_URI") or None,
        oidc_role_claim=get("OIDC_ROLE_CLAIM", "roles"),
        oidc_role_mapping=_json_dict(get("OIDC_ROLE_MAPPING")),
        oidc_session_ttl_seconds=env_int("OIDC_SESSION_TTL_SECONDS", 28800),
        otel_exporter_endpoint=get("OTEL_EXPORTER_OTLP_ENDPOINT") or None,
        otel_service_name=get("OTEL_SERVICE_NAME", "opencti-ioc-hub"),
    )
