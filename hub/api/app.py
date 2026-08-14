"""Admin API: servicio separado del consumidor de eventos de OpenCTI, expone
OpenAPI 3.1. `create_app(config)` construye una instancia aislada (estado
propio en `app.state.hub`) para que tests puedan levantar varias apps
independientes contra distintos `state_dir` de prueba.

Autor: Athan Espinoza
"""
import os
import time
import uuid
from collections import defaultdict, deque

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from hub import __version__
from hub.api.audit_store import init_db as init_audit_db
from hub.api.deps import APIState
from hub.api.errors import api_error_handler, APIError, http_exception_handler, validation_exception_handler
from hub.api.idempotency_store import init_db as init_idempotency_db
from hub.api.routers import alerts, audit, deliveries, destinations, events, feeds, health, ingestion, oidc_auth, policies, secrets, taxii
from hub.api.token_store import init_db as init_tokens_db
from hub.config import HubConfig
from hub.cursor_store import init_db as init_cursor_db
from hub.destinations_store import init_db as init_destinations_db
from hub.errors import ProblemDetail
from hub.graphql_client import GraphQLClient
from hub.alerting_store import init_db as init_alerts_db
from hub.ingestion_control import init_db as init_ingestion_control_db
from hub.ledger import init_db as init_ledger_db
from hub.policy_store import init_db as init_policies_db
from hub.oidc_session_store import init_db as init_oidc_sessions_db
from hub.secret_encryption import load_cipher
from hub.secrets_store import init_db as init_secrets_db
from hub.taxii_store import init_db as init_taxii_db
from hub.tracing import configure_tracing


class _InMemoryRateLimiter:
    """Rate limit por usuario/IP, expuesto via encabezados RateLimit/
    RateLimit-Policy. Implementacion de MVP: vive en memoria del proceso, no
    sobrevive un restart ni escala a mas de un worker; un backend
    compartido (por ejemplo Redis) queda pendiente para cuando el
    despliegue lo requiera."""

    def __init__(self, *, limit: int = 120, window_seconds: int = 60):
        self.limit = limit
        self.window_seconds = window_seconds
        self._hits: dict = defaultdict(deque)

    def check(self, key: str, *, now: float = None) -> tuple[bool, int]:
        now = now if now is not None else time.time()
        hits = self._hits[key]
        while hits and now - hits[0] > self.window_seconds:
            hits.popleft()
        if len(hits) >= self.limit:
            return False, 0
        hits.append(now)
        return True, self.limit - len(hits)


def create_app(config: HubConfig) -> FastAPI:
    configure_tracing(config)
    app = FastAPI(
        title="OpenCTI IOC Distribution Hub - Admin API",
        version=__version__,
        openapi_url="/admin/api/v1/openapi.json",
        docs_url="/admin/api/v1/docs",
    )

    os.makedirs(config.state_dir, exist_ok=True)
    # Una base sqlite por dominio (destinos, politicas, ledger, tokens...) en
    # vez de una unica base compartida: aisla el estado de cada area y hace
    # mas facil resetear/inspeccionar una sola parte sin tocar el resto.
    app.state.hub = APIState(
        config=config,
        destinations_conn=init_destinations_db(os.path.join(config.state_dir, "destinations.sqlite3")),
        policies_conn=init_policies_db(os.path.join(config.state_dir, "policies.sqlite3")),
        ledger_conn=init_ledger_db(os.path.join(config.state_dir, "ledger.sqlite3")),
        tokens_conn=init_tokens_db(os.path.join(config.state_dir, "tokens.sqlite3")),
        idempotency_conn=init_idempotency_db(os.path.join(config.state_dir, "idempotency.sqlite3")),
        audit_conn=init_audit_db(os.path.join(config.state_dir, "audit.sqlite3")),
        ingestion_control_conn=init_ingestion_control_db(os.path.join(config.state_dir, "ingestion_control.sqlite3")),
        cursor_conn=init_cursor_db(os.path.join(config.state_dir, "cursor.sqlite3")),
        taxii_conn=init_taxii_db(os.path.join(config.state_dir, "taxii.sqlite3")),
        alerts_conn=init_alerts_db(os.path.join(config.state_dir, "alerts.sqlite3")),
        secrets_conn=init_secrets_db(os.path.join(config.state_dir, "secrets.sqlite3")),
        secret_cipher=load_cipher(config),
        oidc_sessions_conn=init_oidc_sessions_db(os.path.join(config.state_dir, "oidc_sessions.sqlite3")),
        graphql_client=GraphQLClient(config.opencti_url, config.opencti_token, verify=config.verify),
    )
    app.state.rate_limiter = _InMemoryRateLimiter()

    # Normaliza toda respuesta de error (interna, HTTP generica, o de
    # validacion de payload) al mismo formato Problem Details.
    app.add_exception_handler(APIError, api_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)

    # CORS cerrado por defecto (lista vacia si ADMIN_UI_ORIGINS no esta
    # seteado), habilitado solo al origen real de la UI Angular cuando se
    # configura. `allow_credentials=True` porque la cookie de sesion OIDC
    # (`hub_session`) necesita CORS credenciado -- por eso `admin_ui_origins`
    # nunca puede ser wildcard (los navegadores rechazan `*` + credentials).
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.admin_ui_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def _correlation_and_security(request: Request, call_next):
        # Se respeta un X-Correlation-Id entrante (para trazar una cadena de
        # llamadas entre servicios) y se genera uno nuevo si el cliente no
        # manda ninguno.
        cid = request.headers.get("X-Correlation-Id") or str(uuid.uuid4())
        request.state.correlation_id = cid

        # El rate limit se identifica por token cuando hay Authorization, o
        # por IP del cliente en caso contrario, para que clientes distintos
        # sin autenticar no compartan un unico bucket global.
        actor_key = request.headers.get("Authorization") or (request.client.host if request.client else "anon")
        allowed, remaining = app.state.rate_limiter.check(actor_key)
        if not allowed:
            problem = ProblemDetail(
                type="https://hub.local/problems/rate_limited",
                title="Too Many Requests",
                status=429,
                detail="rate limit exceeded",
                instance=str(request.url.path),
                error_code="rate_limited",
                correlation_id=cid,
            )
            return JSONResponse(
                status_code=429,
                content=problem.model_dump(exclude_none=True),
                media_type="application/problem+json",
                headers={
                    "X-Correlation-Id": cid,
                    "RateLimit": f"limit={app.state.rate_limiter.limit}, remaining=0",
                    "RateLimit-Policy": f"{app.state.rate_limiter.limit};w={app.state.rate_limiter.window_seconds}",
                },
            )

        response = await call_next(request)
        response.headers["X-Correlation-Id"] = cid
        # Headers de seguridad basicos en toda respuesta: evitan que el
        # browser adivine el content-type (MIME sniffing) y evitan filtrar
        # la URL interna via el header Referer hacia un origen externo.
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["RateLimit"] = f"limit={app.state.rate_limiter.limit}, remaining={remaining}"
        response.headers["RateLimit-Policy"] = f"{app.state.rate_limiter.limit};w={app.state.rate_limiter.window_seconds}"
        return response

    app.include_router(health.router)
    app.include_router(destinations.router)
    app.include_router(policies.router)
    app.include_router(deliveries.router)
    app.include_router(feeds.router)
    app.include_router(audit.router)
    app.include_router(events.router)
    app.include_router(ingestion.router)
    app.include_router(taxii.router)
    app.include_router(alerts.router)
    app.include_router(secrets.router)
    app.include_router(oidc_auth.router)

    return app
