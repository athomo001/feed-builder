# Changelog

**Autor:** Athan Espinoza

Formato basado en [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/) y versionado según [SemVer](https://semver.org/lang/es/). Las versiones del Hub (`hub/__version__`) están alineadas a las Entregas de [spec/09-ROADMAP-ACCEPTANCE.md](spec/09-ROADMAP-ACCEPTANCE.md): `0.1.0` = Entrega 0, `0.1.1` = Entrega 1, `0.1.2` = Entrega 2, y así sucesivamente, cambios importantes seria  0.2.0 y cambios nivel estructura  serian 1.0.0, 2.0.0 . El script legado `opencti_feed_builder.py` no versiona por separado; sus cambios relevantes también quedan registrados aquí.

## [0.1.8] - 2026-08-14 - Corrección de despliegue y limpieza de comentarios

Sin cambios funcionales en `hub/`. Dos correcciones acumuladas a lo largo de las Entregas 0-5 que nunca se habían revisado:

### Corregido

- **`docker-compose.yml`/`feed-builder.yml`/`.env`/`.env.example`/`nginx.conf` desplegaban todo el stack de OpenCTI** (redis, elasticsearch, minio, rabbitmq, opencti, worker, conectores) más el script legado `opencti_feed_builder.py`, en vez de únicamente el Hub (`hub-service`+`hub-api`+`nginx`) contra una instancia de OpenCTI ya desplegada aparte — ver `spec/03-ARCHITECTURE.md` para las dos topologías soportadas (co-ubicado vía red Docker externa, o remoto vía HTTPS). Reescritos los 5 archivos; `nginx.conf` reduce su alcance a TLS + `/feeds/` + reverse-proxy a `hub-api` (`/admin/api/`, `/taxii2/`, `/healthz/`) + servir el build estático de Angular, sin proxy a `opencti:8080`/`/stream/`.
- **`nginx.conf.example` eliminado** por redundante (duplicaba `nginx.conf`, que ya es la plantilla genérica vía `envsubst`), pero la regla `nginx.conf` / `!nginx.conf.example` en `.gitignore` (pensada para cuando `nginx.conf` traía IP/hostname reales hardcodeados) no se actualizó junto con eso — dejaba el nuevo `nginx.conf`, ya genérico, completamente fuera de control de versiones sin avisar. Corregida la regla: `nginx.conf` se versiona.
- **Comentarios de código citaban su origen interno** (`spec/NN-....md`, "Entrega N", `AUDITORIA.md`) en los 77 archivos de `hub/` y los 3 de `scripts/` — información de seguimiento de proyecto sin valor para quien simplemente lee el código. Eliminadas las citas conservando la razón de fondo cuando la había (por qué se tomó tal decisión de diseño, no de dónde salió el requisito). Se agregó `Autor: Athan Espinoza` a los 80 archivos (antes solo estaba en unos pocos) y comentarios de flujo donde antes solo había un docstring al inicio del archivo y nada más (ejemplo puntual señalado por el usuario: `_COLUMNS` en `hub/destinations_store.py` ahora explica por qué existe esa lista de columnas).

Verificación tras ambos cambios: `pytest` (473 tests), `ng build`, `ng test`, `npx playwright test` y `docker compose config` (ambos archivos) en verde.

## [0.1.7] - 2026-08-14 - Entrega 5: Producción

`spec/09-ROADMAP-ACCEPTANCE.md` "Entrega 5": PostgreSQL escalable, OIDC/SSO y RBAC, secret manager, OpenTelemetry Collector, endurecimiento de producción (load test, chaos/recovery, backup/restore, rotación). El roadmap dejaba 2 decisiones abiertas directamente relevantes (#2 SQLite vs PostgreSQL, #5 OIDC/SSO vs usuario/contraseña local); se fijaron explícitamente — ver "Decisiones fijadas" abajo.

### Agregado

- **Secret manager (cifrado en reposo)**: `hub/secret_encryption.py` (Fernet/`cryptography`, clave nunca en la DB ni en `state_dir`) + `hub/secrets_store.py`. `hub/credentials.py::resolve_credential_ref` gana el esquema `secret://<name>` junto al `env://` ya existente — mismo punto único de resolución, ambos conviven. `hub/api/routers/secrets.py`: `POST`/`GET`/`DELETE /admin/api/v1/secrets`, `.../test` (descifra y confirma sin exponer el valor), `.../rotate-key` (re-cifra todo, rol `security-admin`). `secrets_conn`/`cipher` enhebrados por los ~6 call sites existentes que resuelven `credential_ref` (`HttpPushAdapter`, `QRadarAdapter`, el router TAXII, los dos canales de alerta).
- **OIDC/SSO**: cliente OIDC genérico Authorization Code + PKCE (`hub/oidc_client.py`: discovery, JWKS, validación de ID token con algoritmos permitidos explícitos — nunca `alg=none` —, mapeo de claims a rol) + sesión por cookie HttpOnly+Secure+SameSite=Lax (`hub/oidc_session_store.py`, mismo patrón de hash que `hub/api/token_store.py`). `hub/api/routers/oidc_auth.py`: `/auth/oidc/login`\|`/callback`, `/auth/logout`, `/auth/whoami`. `hub/api/auth.py::require_role` acepta Bearer **o** sesión OIDC indistintamente. Los API tokens de Entrega 2 se mantienen sin cambios para automatización — spec/08 los trata como mecanismo separado, no reemplazado. UI: botón "Entrar con SSO" (navegación de página completa, no una ruta Angular), `AuthService` detecta una sesión existente al arrancar (`GET /auth/whoami` via `provideAppInitializer`) sin guardar nada persistente del lado del cliente.
- **OpenTelemetry**: `hub/tracing.py`, aditivo/opcional por diseño (spec/06: sin `OTEL_EXPORTER_OTLP_ENDPOINT`, el tracer global de OTel ya es no-op, cero guards condicionales en el código instrumentado). Los 7 spans de spec/06 sección 4 instrumentados: `opencti.stream.receive`/`opencti.event.normalize` (`hub/service.py`, `hub/pipeline.py`), `policy.evaluate` (`hub/pipeline.py`), `delivery.render`/`.send`/`.acknowledge` (`hub/delivery_runner.py`), `feed.rebuild` (`hub/txt_feed.py`, `hub/stix_bundle.py`). Config de ejemplo del Collector en `deploy/otel-collector-config.yaml` con un processor que filtra atributos con pinta de secreto antes de exportar.
- **Endurecimiento de producción**: `scripts/backup_state.py`/`restore_state.py` (tar de `HUB_STATE_DIR`+`TXT_FEED_DIR`, timestamped, protegido contra path traversal al restaurar, rechaza sobrescribir sin `--force`); `tests/hub/test_chaos_opencti_outage.py` (conexión SSE simulada que falla y se recupera, verifica el backoff/reconexión ya construido desde Entrega 1 — hueco de cobertura que `tests/hub/test_service.py` no ejercitaba); `scripts/load_test.py` (carga concurrente simple contra el Admin API, sin dependencia nueva). `docs/RUNBOOK.md` nuevo: procedimientos de backup/restore (+ prueba mensual, spec/06), recuperación ante caída de OpenCTI, rotación de la clave de cifrado de secretos, recuperación de dead-letter.

### Decisiones fijadas (spec/09 las dejaba abiertas)

- **#2 PostgreSQL**: solo se documenta el camino de migración (`spec/PROJECT-MAP.md` "Pendiente conocido: Entrega 5"), no se escribe código Postgres en esta pasada — los 12 módulos `hub/*_store.py` siguen sobre SQLite.
- **#5 OIDC/SSO**: cliente genérico probado contra un IdP simulado (mismo patrón de honestidad que QRadar/TAXII en Entrega 4), sin fallback de usuario/contraseña local; API tokens de Entrega 2 sin cambios.
- **Secret manager**: cifrado en reposo con clave externa, no una integración con un secret manager externo real (Vault u otro) — spec/08 acepta ambos como equivalentes.

### Conocido / pendiente

- Migración real a PostgreSQL: solo documentada, sin driver ni código de conexión en el repo.
- Sin integración con un secret manager externo real (HashiCorp Vault, AWS Secrets Manager, etc.).
- OIDC sin validar contra un IdP real (Keycloak/Okta/Azure AD) — probado contra un simulador con keypair RSA propio.
- Chaos test acotado a la caída de OpenCTI: sin chaos test de caída de un destino o de una base SQLite puntual.
- Load test simple, no una herramienta de carga real (k6/locust/gatling).
- Archivo histórico de IOC (spec/06) y SLO de latencia por destino (Decisión #9) siguen sin resolver.

## [0.1.6] - 2026-08-14 - Entrega 4: Integraciones

Adapters de destino priorizados por esfuerzo real (`spec/05-FORMATS-DESTINATIONS.md` "Modos de entrega y esfuerzo relativo"), servidor TAXII 2.1 propio y alertas email/webhook (`spec/09-ROADMAP-ACCEPTANCE.md` Entrega 4). El roadmap dejaba 6 decisiones abiertas para esta entrega (#4, #8, #9, #10, #11, #12); se fijaron explícitamente en vez de dejarlas sin resolver — ver "Decisiones fijadas" abajo.

### Agregado

- Refactor previo: `hub/adapters/factory.py::build_adapter()` centraliza el dispatch por `destination.adapter`, antes triplicado en `hub/service.py`, `hub/api/routers/destinations.py` y `hub/api/routers/deliveries.py` (este último construía incorrectamente `HttpPushAdapter` para cualquier adapter que no fuera `txt_feed`, bug preexistente que este refactor corrige de paso).
- Bajo esfuerzo (Fortinet/FortiGate, Palo Alto EDL, pfSense/pfBlockerNG, Cisco Security Intelligence): **sin adapter nuevo** — los cuatro reusan `TxtFeedAdapter` tal cual, exactamente como sugiere spec/05.
- `FeedWriter` (`hub/txt_feed.py`) generalizado con `render_line`/`parse_line`/`header` opcionales (default = comportamiento idéntico al TXT plano de Entrega 1) para que los 3 formatos `file_feed` nuevos reusen atomic-write/capacidad/overflow/dedup en vez de triplicar esa lógica.
- `CsvFeedAdapter` (`csv_feed`, Check Point): columnas configurables (default: las recomendadas por spec/05), separador/quoting/header configurables, protección contra CSV injection (`=`/`+`/`-`/`@`).
- `MikrotikAdapter` (`mikrotik_rsc`, RouterOS `.rsc`): genera el bloque `add address=... list=... timeout=...`; `timeout` se deriva de `valid_until` recalculado en cada rebuild. Restringido a IOC de familia `network` (`address-list` no acepta dominios ni hashes). Sin plantilla `.rsc` completa (spec/05 no la documenta) — el operador envuelve el bloque en su propio Scheduler/`/tool fetch`.
- `WazuhCdbAdapter` (`wazuh_cdb`): CDB `clave:valor`. **Decisión #11**: el Hub solo materializa el archivo; sincronizarlo al filesystem del manager y disparar el reload es responsabilidad externa (no se construyó un agente/sync-companion con su propia superficie de credenciales SSH).
- `QRadarAdapter` (`qradar_reference_set`): `POST .../api/reference_data/sets/{name}/bulk_load` (aditivo, JSON array), header `SEC` vía `credential_ref`. No validado contra una instancia QRadar real (mismo disclaimer que `HttpPushAdapter` desde Entrega 2).
- `hub/stix_bundle.py`: `render_stix_indicator`/`render_stix_bundle` (STIX 2.1; hashes usa el vocabulario abierto de STIX, sin convertir algoritmos sin nombre "famoso" — sha224/sha384/imphash/etc. — a otro que no son) y `StixBundleWriter` (mismo patrón que `FeedWriter` pero un solo JSON con array `objects`, no una línea por valor). `StixBundleAdapter` (`stix_bundle_feed`) materializa un `bundle.json` por destino.
- **Decisión #4 (TAXII)**: el Hub construye su propio servidor TAXII 2.1 mínimo y de solo lectura (`hub/taxii_store.py`, `hub/adapters/taxii_adapter.py`, `hub/api/routers/taxii.py`, montado en `/taxii2/`) en vez de depender solo del TAXII nativo de OpenCTI — es el Hub quien filtra por política antes de exponer el IOC a un tercero como Cisco TID. Un API Root (`hub`), una colección por destino `taxii2`, auth HTTP Basic contra `credential_ref` (convención `usuario:password`). `discard()` republica el objeto con `revoked: true` en vez de borrarlo (una colección TAXII es append/update-only, a diferencia de `stix_bundle_feed` que sí remueve el objeto porque es una foto del estado actual).
- Alertas (`hub/alerting_store.py`, `hub/alert_rules.py`, `hub/alerting.py`, `hub/alert_channels.py`, `hub/webhook_signing.py`, `hub/api/routers/alerts.py`): 5 de las 11 condiciones de `spec/06` sección 5 con señal real disponible hoy (OpenCTI desconectado, cursor sin avanzar, dead-letter no vacío, destino sin entrega exitosa reciente, feed sin rebuild reciente). Persistencia con dedup por `(condition, component, resource_id)` y cooldown configurable. Canales email (SMTP) y webhook (firmado HMAC-SHA256 estilo Standard Webhooks). `POST /alerts/evaluate` sin scheduler real (manual o cron externo, mismo patrón que `POST /deliveries/{id}/retry`); `hub.service` además evalúa las 2 condiciones in-process una vez por minuto en su propio loop.
- UI Angular: dropdown de adapter en el formulario de destino gana los 6 tipos nuevos + editor `format_options` (JSON, gap preexistente que no se editaba desde el formulario, cerrado de paso). Panel "Alertas activas" en Overview (reconocer para rol `operator`) — no se agregó una 8va sección de navegación, spec/07 fija 7. 2 tests E2E nuevos (alerta reconocida, alta de destino con adapter nuevo).

### Decisiones fijadas (spec/09 las dejaba abiertas)

- **#4 TAXII**: servidor propio del Hub (ver arriba).
- **#11 Wazuh**: solo materializar el archivo, sin agente/sync-companion (ver arriba).
- **#12 orden**: bajo → medio → alto esfuerzo, según sugiere spec/05.
- **#8/#9/#10 (overflow default, SLO por destino, archivo histórico)**: fuera de alcance de esta pasada — ver "Conocido / pendiente".

### Conocido / pendiente

- Archivo histórico de IOC (spec/06): no está en los bullets de "Entrega 4" del roadmap, más cercano a Entrega 5 ("Producción"). No construido.
- Cola/workers real (spec/03): sigue sin construir; los 6 adapters nuevos usan el mismo `hub/delivery_runner.py` de un intento por llamada.
- SLO de latencia por destino (Decisión #9): sigue abierta; el alerta de destino usa un umbral único a nivel Hub, no configurable por destino.
- 6 de las 11 condiciones de alerta de spec/06 sin señal disponible (espacio en disco, TLS inválido/credencial rechazada, caída de volumen histórico, entre otras) — no se inventaron valores falsos.
- QRadar y TAXII sin validar contra una instancia/consumidor real.
- Check Point CSV, MikroTik `.rsc` y Wazuh CDB: spec/05 no confirma el esquema/sintaxis exacto de cada fabricante real — implementado con las columnas/sintaxis genéricas documentadas.

## [0.1.5] - 2026-08-13 - Entrega 3: UI Angular (Admin Dashboard)

Consola operativa completa sobre el Admin API de Entrega 2, según `spec/07-ADMIN-UI-ANGULAR.md`. Workspace nuevo en `ui/` (Angular **21.2.21**, no 22.x como cita spec/07 v22.1.1 — Node v24.13.0 del entorno de build no satisface el requisito de Angular CLI 22.x, ≥24.15.0/22.22.3/26, y no había gestor de versión de Node disponible para subir; Angular 21.x acepta Node ≥24.0.0. Sustitución documentada, no oculta).

### Agregado

- `core/`: `AuthService` (token+rol en memoria únicamente, sin `localStorage`/`sessionStorage` — spec/07 "Seguridad frontend": la sesión se pierde al refrescar, por diseño), interceptor funcional (`Authorization` + `X-Correlation-Id`, maneja 401/403/5xx), `role.guard.ts` (jerarquía calcada de `hub/api/token_store._ROLE_RANK`), `pollingSignal` (RxJS `interval`+`switchMap` sobre el REST plano, con `.refresh()` para forzar una re-consulta inmediata tras una acción exitosa en vez de esperar el próximo tick), modelos TS 1:1 con los schemas del Admin API, y un servicio HTTP delgado por recurso.
- Las 7 secciones de navegación de spec/07: Overview (KPIs + matriz de destinos), Observabilidad & Logs (buscador/timeline del Event Ledger), Operaciones & DLQ (retry/discard con motivo obligatorio + feeds materializados), Políticas (crear borrador → simular → publicar/rollback con motivo obligatorio → historial), Destinos (alta/edición, probar conexión, pausar/reanudar), OpenCTI/Ingesta (pausar/reanudar/reconciliar/rebobinar), Auditoría (tabla filtrable + export CSV client-side).
- `shared/confirm-dialog` (modal obligatorio para toda acción destructiva/irreversible, con campo de motivo opcional u obligatorio) y `shared/status-badge` (verde/ámbar/rojo/gris por estado).
- Backend, gap cerrado para que Políticas pudiera cumplir spec/07: `POST /policies/{id}/publish` y `/rollback` ahora exigen `reason` (`hub/api/schemas.py VersionRequest`, `hub/api/routers/policies.py`, tests en `tests/hub/test_api_policies.py`).
- `ADMIN_UI_ORIGINS` (`hub/config.py`, `hub/api/app.py`): CORS cerrado por defecto (spec/08) bloqueaba también a la UI Angular real corriendo en otro origen — se agregó esta variable de entorno para habilitar explícitamente el origen de la UI, sin abrir CORS a cualquier origen.
- Suite E2E con Playwright (`ui/e2e/`): backend Python + `ng serve` coordinados por `playwright.config.ts` (`globalSetup`/`globalTeardown`), con estado pre-sembrado (token `security-admin`, una entrega en dead-letter) que se limpia en cada corrida para que las corridas sean reproducibles. 5 tests: login (redirect sin sesión, token válido, token inválido), discard de DLQ con motivo obligatorio, y el flujo completo de políticas (crear → simular con error esperado sin OpenCTI real → publicar con motivo).

### Corregido

- Ninguna acción mutante (crear/editar/pausar/reanudar destino, descartar/reintentar entrega, reconstruir feed, crear/publicar/hacer rollback de política, pausar/reanudar/reconciliar/rebobinar ingestión) refrescaba su lista en pantalla de inmediato — dependían del próximo tick del polling (hasta 15s), así que una acción exitosa podía parecer no haber pasado nada. Encontrado corriendo el E2E completo (no era visible probando cada página por separado). Se agregó `.refresh()` a `pollingSignal` y se conectó tras cada mutación exitosa.
- El E2E de políticas fallaba de forma intermitente por estado no aislado entre corridas: `ui/e2e/seed_and_run_backend.py` no borraba el `state_dir` previo, así que un destino/política de una corrida anterior (o de un intento fallido a medio camino) contaminaba la siguiente corrida (por ejemplo, dos versiones "draft" en vez de una recién creada). Se agregó limpieza (`shutil.rmtree`) del `state_dir`/`feeds_dir` al inicio de cada corrida.

### Decisión de alcance

- Sin SSE/WebSocket: todas las vistas "en vivo" usan polling corto (spec/07 lo acepta como fallback válido).
- Rebobinar cursor pide motivo y muestra cursor actual vs. solicitado, **sin preview de volumen** afectado — no hay forma barata de contar eventos entre dos cursores sin re-consultar OpenCTI extensamente; se documenta como límite conocido en vez de simular un número.

### Conocido / pendiente

- **Visor de logs en vivo**: no hay log store ni streaming de logs estructurados (`hub.service`/`hub.api` solo imprimen a stdout) — la sección de Observabilidad lo marca explícitamente como "no disponible" en vez de simularlo.
- **Configuración operativa editable** (umbrales/retención): no existen los endpoints — la sección de Auditoría lo marca explícitamente como "no disponible".
- Sin validar con navegadores reales más allá de Chromium headless vía Playwright en este entorno.

## [0.1.4] - 2026-08-13 - Preparación de backend para Entrega 3 (UI Angular)

Antes de construir la UI Angular de `spec/07-ADMIN-UI-ANGULAR.md`, se cerraron los huecos de backend que esa consola necesita para no mostrar pantallas vacías o falsas.

### Agregado

- Auditoría (`hub/api/audit_store.py`, `hub/api/audit.py`): registro append-only de acciones de operador (actor, acción, recurso, `before`/`after`, motivo, resultado, `correlation_id`), escrito desde los endpoints mutantes de destinos/políticas/entregas/ingestión. `GET /admin/api/v1/audit` con filtros y paginación.
- Búsqueda/paginación del Event Ledger: `search_deliveries` en `hub/ledger.py`; `GET /admin/api/v1/events` (búsqueda) y `GET /admin/api/v1/events/{event_id}` (línea de tiempo). Acotado a las columnas que el ledger ya guarda — sin Canonical Event Store todavía.
- Control de ingestión cross-proceso (`hub/ingestion_control.py`): `hub.service` y `hub.api` son procesos separados sin IPC directo; la API escribe pedidos de pausar/reanudar/reconciliar/rebobinar en SQLite y `listen_live_stream` los sondea y aplica en su propio loop. Router `hub/api/routers/ingestion.py` (`GET /admin/api/v1/ingestion/status`, `POST .../pause`, `.../resume`, `.../reconcile`, `.../rewind` — este último con rol `security-admin` y motivo obligatorio, dejando el cursor previo como checkpoint en la auditoría).

### Decisión de alcance

- Sin SSE/WebSocket real en el Admin API en esta pasada: spec/07 acepta "fallback a polling corto" y se parte directo de ahí. Push real queda como adición aislada futura sobre estos mismos endpoints.

### Conocido / pendiente

- Sin Canonical Event Store (family/subtype/valor por evento) — el Inspector del Event Ledger de la futura UI queda acotado a lo que el ledger ya guarda.
- Sin log store ni streaming de logs — "Visor de logs en vivo" de spec/07 sigue sin backend.

## [0.1.3] - 2026-08-13 - Entrega 2: API y primer destino

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

## [0.1.1] - 2026-08-13 - Entrega 1: Núcleo confiable

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
