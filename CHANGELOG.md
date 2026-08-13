# Changelog

**Autor:** Athan Espinoza

Formato basado en [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/) y versionado según [SemVer](https://semver.org/lang/es/). Las versiones del Hub (`hub/__version__`) están alineadas a las Entregas de [spec/09-ROADMAP-ACCEPTANCE.md](spec/09-ROADMAP-ACCEPTANCE.md): `0.1.0` = Entrega 0, `0.2.0` = Entrega 1, `0.3.0` = Entrega 2, y así sucesivamente. El script legado `opencti_feed_builder.py` no versiona por separado; sus cambios relevantes también quedan registrados aquí.

## [Sin publicar]

Nada pendiente todavía sobre `0.3.0`.

## [0.3.0] - 2026-08-13 - Entrega 2: API y primer destino

### Agregado

- Admin API con FastAPI (`hub/api/`) en `/admin/api/v1` (+ `/healthz/*`): CRUD de destinos y políticas, simulación/publicación/rollback de políticas, retry/discard de entregas, listado/preview/rebuild de feeds, status y métricas estilo Prometheus.
- Autenticación por API token local con roles jerárquicos (`viewer` < `operator` < `policy-admin` < `security-admin`), `Idempotency-Key` real (antes solo validado en forma), errores `application/problem+json` (RFC 9457) con `X-Correlation-Id`, rate limit en memoria y headers de seguridad básicos.
- Motor de políticas configurable (`hub/policy_engine.py`) evaluado contra políticas versionadas e inmutables (`hub/policy_store.py`: `draft` → `published` → `superseded`/`rolled_back`).
- `hub/pipeline.py` reescrito: ya no entrega a un único destino fijo, evalúa cada evento contra la política activa de **todos** los destinos habilitados.
- Adapters de destino con el contrato `validate/render/send/acknowledge/healthcheck/close` (+ `discard`): `TxtFeedAdapter` (`file_feed`, envuelve `hub/txt_feed.py`) y `HttpPushAdapter` (`api_push`, primer destino real elegido: QRadar-shaped, JSON + Bearer).
- Reintentos con backoff/jitter y circuit breaker por destino (`hub/retry.py`, `hub/delivery_runner.py`); dead-letter en el ledger (`attempts`, `list_dead_letters`).
- Resolución de `credential_ref` vía `env://` (`hub/credentials.py`), placeholder documentado hasta que exista un secret manager real (Entrega 5).
- `hub/policy_simulation.py`: simula una política candidata contra una muestra offline o contra OpenCTI en vivo, comparando volumen aceptado antes/después.

### Cambiado

- `FeedWriter` (`hub/txt_feed.py`) ahora siembra su estado desde el archivo en disco al construirse — necesario porque los adapters de Entrega 2 son de vida corta y se reconstruyen por evento/request; sin esto, una segunda instancia habría borrado lo escrito por la primera.
- Todas las conexiones SQLite del proyecto (`cursor_store.py`, `ledger.py`, `destinations_store.py`, `policy_store.py`, `token_store.py`, `idempotency_store.py`) se abren con `check_same_thread=False`: FastAPI ejecuta los handlers síncronos en un threadpool y las conexiones creadas al levantar la app se usan desde otros hilos.

### Conocido / pendiente

- Sin cola/workers real todavía (spec/03 "Queue y workers"); los reintentos son manuales (`POST /deliveries/{id}/retry`) o por automatización externa.
- Adapter HTTP push sin validar contra un QRadar/endpoint real; solo probado con `requests` simulado.
- Rate limiting en memoria del proceso, no distribuido.

## [0.2.0] - 2026-08-13 - Entrega 1: Núcleo confiable

### Agregado

- Loop real de ingestión (`hub/service.py`): conexión al Live Stream vía SSE con reconexión/backoff, apagado cooperativo (SIGTERM/SIGINT) y heartbeat.
- Backfill acotado (`hub/backfill.py`) y reconciliación periódica (`hub/reconcile.py`) vía GraphQL (`hub/graphql_client.py`, `hub/graphql_indicator.py`), reutilizando el mismo clasificador STIX que el Live Stream.
- Framer SSE con límites de tamaño por línea/evento (`hub/sse.py`), incluyendo el `id:` de cada evento como cursor de recuperación.
- Cursor durable con recuperación tras reinicio (`hub/cursor_store.py`).
- `hub/pipeline.py` (versión inicial): normaliza, deduplica (`hub/dedup.py`), filtra revoked/expirado (`hub/ttl.py`) y escribe a un feed TXT compatible (`hub/txt_feed.py`) contra un destino fijo (`txt-feed-default`), a falta de CRUD de destinos.
- Campo `revoked` agregado a `CanonicalIOCEvent` (ya estaba en el modelo de spec/04, faltaba en el código).

### Conocido / pendiente

- Sin validar contra una instancia OpenCTI real; los nombres de campo GraphQL siguen la documentación pública, no un esquema introspectado.

## [0.1.0] - 2026-08-12 - Entrega 0: Contratos y seguridad

### Agregado

- Modelo canónico de evento IOC (`hub/models.py`), `PolicyOutcome`/`ReasonCode` (`hub/policy.py`), `DeliveryState` (`hub/delivery.py`), formato de error RFC 9457 (`hub/errors.py`) y contrato `Idempotency-Key` (`hub/idempotency.py`).
- Fixtures STIX anonimizadas para create/update/delete (`hub/fixtures/`).
- Especificación modular (`spec/01` a `spec/10`) y [PROJECT-MAP.md](spec/PROJECT-MAP.md).
- Correcciones P0/P1/P2 de [AUDITORIA.md](AUDITORIA.md) sobre el script legado `opencti_feed_builder.py`: `HASH_FROM_ANY_EVENT` desactivado por defecto, límites de tamaño SSE, apagado ordenado, heartbeat/healthcheck, `autoindex off` en nginx.
