# Feed Builder para OpenCTI

```bash
!! Esto esta en beta - si que manejese con cuidado - no hay garantias de nada, ni de tu vida como lector como la mia
```

> Historial de cambios por versión en [CHANGELOG.md](CHANGELOG.md). Versión actual del Hub: `hub.__version__` (ver [hub/\_\_init\_\_.py](hub/__init__.py)).
>
> `docker-compose.yml`, `feed-builder.yml`, `nginx.conf` y `.env`/`.env.example` en la raíz del repo despliegan **solo el Hub** (`hub/`, "OpenCTI IOC Distribution Hub", secciones 13 en adelante) — asumen una instancia de OpenCTI ya desplegada por separado, no la levantan. Las secciones 6, 7 y 8 de abajo documentan el script legado `opencti_feed_builder.py` (todavía presente en el repo como referencia histórica) tal como funciona en el código, pero **no** es lo que el `docker-compose.yml` actual despliega.

## 1) Objetivo del proyecto

Este proyecto es el Hub de distribución de IOC (`hub/`, "OpenCTI IOC Distribution Hub"): una integración *sobre* una instancia de OpenCTI ya desplegada, no un despliegue de OpenCTI en sí. Se conecta al Live Stream (SSE) de OpenCTI, aplica políticas de filtrado configurables por destino, y distribuye los IOC resultantes en el formato que cada destino necesita (TXT/CIDR, CSV, RouterOS `.rsc`, CDB de Wazuh, JSON push, STIX 2.1, TAXII 2.1) — ver secciones 13 en adelante para el detalle completo por Entrega, y `spec/` para la especificación.

El script legado `opencti_feed_builder.py` (secciones 6-8 más abajo) era la primera versión de esta idea, ya reemplazada por `hub/`; se mantiene en el repo solo como referencia histórica, no se despliega desde `docker-compose.yml`.

## 2) Que contiene este repositorio

- `docker-compose.yml`
  - Despliega el Hub (`hub-service`, `hub-api`, `nginx`) contra una instancia de OpenCTI externa. No incluye OpenCTI, sus conectores ni su base de datos/cola/búsqueda — eso corre aparte.
- `feed-builder.yml`
  - Respaldo/plantilla de `hub-service`/`hub-api`, para recuperar rápido esos dos servicios si el compose principal se pierde (ver sección 9).
- `nginx.conf`
  - Ingress propio del Hub: TLS, reverse proxy a `hub-api` (Admin API, TAXII, healthchecks), UI Angular estática y publicación de `/feeds/*`.
- `opencti_feed_builder.py`
  - Script legado, ya reemplazado por `hub/` (ver arriba). Las secciones 6-8 documentan cómo funciona ese script puntualmente.

## 3) Arquitectura y flujo

Ver secciones 13 en adelante para la arquitectura real y vigente del Hub (`hub/`). Resumen: OpenCTI Live Stream → `hub.service` (ingesta, normalización, políticas) → Delivery Adapters/Feeds materializados → Event Ledger; `hub.api` expone la Admin API y sirve a la UI Angular. La sección 3 original describía el flujo del script legado `opencti_feed_builder.py` (sección 6 abajo tiene el detalle completo de ese script específico).

## 4) Requisitos para funcionar

## 4.1 Infraestructura y sistema

- Docker Engine + Docker Compose.
- Una instancia de OpenCTI ya desplegada y accesible (por `OPENCTI_URL`), con un token de cuenta de servicio no administrativa.
- Volúmenes/rutas de host disponibles para (ver `.env.example`):
  - `HUB_APP_PATH` (código del repo, con `hub/`)
  - `UI_DIST_PATH` (salida de `ng build`, ver sección 15.2)
  - `NGINX_CONF_PATH`, `NGINX_CERTS_PATH` (certificado y llave TLS)
- Si el Hub está co-ubicado con OpenCTI en el mismo host: acceso a la red Docker externa de OpenCTI (`OPENCTI_DOCKER_NETWORK`). Si es remoto: solo necesita alcanzar `OPENCTI_URL` por HTTPS.

## 4.2 Requisitos de OpenCTI

- OpenCTI arriba y saludable, gestionado por fuera de este repositorio.
- Token de cuenta de servicio (no administrativa) con permisos de stream y GraphQL — ver sección 13.2.

## 4.3 Requisitos de Nginx

- `nginx.conf` es una plantilla: `server_name`, la redirección HTTP->HTTPS y las rutas de certificado usan `${PUBLIC_HOST}`/`${NGINX_HTTPS_PORT}`, resueltos con `envsubst` al arrancar el contenedor (ver servicio `nginx` en `docker-compose.yml`). No hardcodear IPs/hosts en este archivo.
- Certificados presentes, con nombre de archivo igual a `PUBLIC_HOST` (variable en `.env`):
  - /etc/nginx/certs/\$PUBLIC_HOST.crt
  - /etc/nginx/certs/\$PUBLIC_HOST.key
- Puerto HTTPS publicado (`NGINX_HTTPS_PORT` en `.env`, 8446 por defecto).

## 5) Puesta en marcha

1. Confirmar que OpenCTI ya está arriba y accesible desde donde se va a correr este compose.
2. Copiar `.env.example` a `.env` y completar valores reales (`OPENCTI_URL`, `OPENCTI_SERVICE_ACCOUNT_TOKEN`, rutas de host, `PUBLIC_HOST`).
3. Verificar que las rutas de host usadas en volúmenes existan y tengan permisos.
4. Levantar el Hub:

```bash
docker compose up -d
```

5. Validar estado:

```bash
docker compose ps
docker compose logs -f hub-service
docker compose logs -f hub-api
docker compose logs -f nginx
```

6. Probar la Admin API y los feeds desde red interna:

```bash
curl -k https://$PUBLIC_HOST:$NGINX_HTTPS_PORT/healthz/liveness
curl -k https://$PUBLIC_HOST:$NGINX_HTTPS_PORT/feeds/<destino>/<subtipo>.txt
```

## 6) Como funciona el script opencti_feed_builder.py (legado, referencia histórica)

> Esta sección documenta el comportamiento del archivo `opencti_feed_builder.py` tal como está escrito en el repo. Ya no se despliega desde `docker-compose.yml` (reemplazado por `hub/`, secciones 13 en adelante) — queda como referencia de cómo funcionaba la primera versión.

## 6.1 Inicializacion

- Crea FEEDS_DIR si no existe.
- Inicializa cabeceras de ip.txt, url.txt y hash.txt.
- Carga estado desde state.json:
  - seen_ids: IDs STIX ya vistos (deduplicacion por ID).
  - seen_values: IOC ya vistos con timestamp de expiracion.
  - pending: cola por tipo (ip/url/hash) para modo batch.

Si detecta migracion de formato de estado, limpia y reconstruye feeds para evitar IOC expirados heredados.

## 6.2 Consumo del stream

- Se conecta a:
  - OPENCTI_URL/stream/{STREAM_ID} si STREAM_ID viene definido.
  - OPENCTI_URL/stream en caso contrario.
- Procesa eventos SSE acumulando lineas data: hasta linea vacia (fin de evento).

## 6.3 Extraccion y clasificacion de IOC

Para cada evento:

1. Intenta parsear JSON.
2. Normaliza payload (dict o JSON embebido en string).
3. Extrae STIX desde payload.data.data, payload.data o payload directo.
4. Si es indicator:
   - Extrae observable_type y valor desde extension/pattern/name.
   - Clasifica como ip/url/hash.
5. Si no es indicator y PROCESS_OBSERVABLE_HASHES=true:
   - Busca hashes SHA-256 en observables y tambien en cualquier texto del payload (si HASH_FROM_ANY_EVENT=true).

## 6.4 Filtros aplicados

Filtros base (todos los tipos):

- revoked != true
- valid_until no expirado (si existe)
- TLP permitido por ALLOW_TLP (si no hay label TLP, pasa)
- TTL maximo por tipo:
  - MAX_AGE_DAYS_IP
  - MAX_AGE_DAYS_URL
  - MAX_AGE_DAYS_HASH

Filtros de calidad (principalmente indicator):

- score >= MIN_SCORE
- confidence >= MIN_CONFIDENCE
- detection obligatorio solo si REQUIRE_DETECTION=true

Excepcion para hashes:

- Si HASH_RELAX_FILTERS=true, los hashes de indicator usan solo filtros base (mas flexible para no perder hash utiles).

## 6.5 Escritura de feeds

Dos modos:

- Modo inmediato (WRITE_INTERVAL_SEC <= 0):
  - Cada IOC valido actualiza estado y dispara rebuild del archivo de su feed.
- Modo batch (WRITE_INTERVAL_SEC > 0):
  - Encola IOC en pending y hace flush periodico.
  - En cada flush, actualiza seen_values y reconstruye feeds.

La reconstruccion deja solo IOC vigentes (no expirados).

## 6.6 Backfill inicial

Si BACKFILL_ENABLED=true, consulta GraphQL de OpenCTI para recorrer indicadores recientes y extraer hashes historicos dentro de la ventana BACKFILL_DAYS, con paginacion controlada por BACKFILL_PAGE_SIZE y BACKFILL_MAX_PAGES.

## 6.7 Persistencia y compactacion

- Guarda state.json de forma atomica (tmp + replace).
- Purga IOC expirados de memoria.
- Reintenta conexion SSE con backoff exponencial ante error.

## 7) Variables de configuracion clave

## 7.1 Variables del feed-builder realmente usadas por el script

- OPENCTI_URL
- OPENCTI_TOKEN
- STREAM_ID
- FEEDS_DIR
- MIN_SCORE
- MIN_CONFIDENCE
- ALLOW_TLP
- REQUIRE_DETECTION
- DEBUG_DUMP_ONCE
- STATS_EVERY_SEC
- PROCESS_OBSERVABLE_HASHES
- WRITE_INTERVAL_SEC
- HASH_FROM_ANY_EVENT
- HASH_ONLY_SHA256
- HASH_RELAX_FILTERS
- BACKFILL_ENABLED
- BACKFILL_DAYS
- BACKFILL_PAGE_SIZE
- BACKFILL_MAX_PAGES
- PUBLIC_FEEDS_BASE_URL
- MAX_AGE_DAYS_IP
- MAX_AGE_DAYS_URL
- MAX_AGE_DAYS_HASH

## 7.2 Variables declaradas en docker-compose pero no implementadas en el script actual

- MAX_RECORDS_PER_FEED
- URL_STRIP_SCHEME
- URL_KEEP_QUERY

Nota importante:

- Existe contexto funcional de limite de 20.000 eventos/IOC en la presentacion de la herramienta.
- En este codigo actual, ese limite no se aplica explicitamente con MAX_RECORDS_PER_FEED.
- El control real de crecimiento hoy depende de TTL + deduplicacion + compactacion/rebuild.

## 8) Que se modifica normalmente

## 8.1 Ajustes operativos frecuentes

- Umbral de calidad:
  - MIN_SCORE
  - MIN_CONFIDENCE
  - REQUIRE_DETECTION
- Comparticion TLP:
  - ALLOW_TLP
- Retencion/vida util por tipo:
  - MAX_AGE_DAYS_IP
  - MAX_AGE_DAYS_URL
  - MAX_AGE_DAYS_HASH
- Rendimiento y latencia de escritura:
  - WRITE_INTERVAL_SEC
- Alcance de relleno historico:
  - BACKFILL_ENABLED
  - BACKFILL_DAYS
  - BACKFILL_PAGE_SIZE
  - BACKFILL_MAX_PAGES

## 8.2 Ajustes de publicacion

- Puerto HTTPS expuesto por Nginx (NGINX_HTTPS_PORT).
- Host/IP publico (PUBLIC_HOST en .env): resuelve `server_name` y las rutas de certificado en nginx.conf via envsubst, y compone PUBLIC_FEEDS_BASE_URL. Nunca hardcodear el host en nginx.conf ni en docker-compose.yml directamente.
- Certificados TLS nombrados como `$PUBLIC_HOST.crt`/`$PUBLIC_HOST.key`.
- URL publica de referencia en logs (PUBLIC_FEEDS_BASE_URL, ahora compuesta desde PUBLIC_HOST + NGINX_HTTPS_PORT en docker-compose.yml/feed-builder.yml).

## 9) Respaldo de docker-compose

En este repositorio hay dos piezas relacionadas:

- `docker-compose.yml`:
  - Archivo principal: `hub-service`, `hub-api` y `nginx`.
- `feed-builder.yml`:
  - Respaldo/plantilla de `hub-service`/`hub-api` (no incluye `nginx`).
  - Sirve para recuperar rápidamente esos dos servicios en caso de cambios o pérdida en el compose principal: `docker compose -f feed-builder.yml up -d`.

Práctica recomendada:

- Mantener ambos sincronizados cuando se cambian variables, volúmenes o command de `hub-service`/`hub-api`.

## 10) Operacion y troubleshooting

## 10.1 Comandos utiles

```bash
docker compose logs -f hub-service
docker compose logs -f hub-api
docker compose logs -f nginx
docker compose restart hub-service
docker compose restart hub-api
docker compose restart nginx
```

## 10.2 Problemas comunes

1. 401 en el stream de OpenCTI:
   - `OPENCTI_SERVICE_ACCOUNT_TOKEN` inválido, revocado o sin permisos de stream/GraphQL.

2. Feeds vacíos:
   - No hay eventos que cumplan la política publicada del destino (ver sección 14).
   - No hay ninguna política publicada para ese destino (spec/08: sin política publicada, el destino no recibe entregas).

3. Nginx responde 404 en `/feeds/*`:
   - Volumen `hub_feeds` mal montado, o el destino/subtipo pedido todavía no generó ningún archivo.

4. Problemas TLS:
   - Certificado/llave no encontrados o no coinciden con `server_name`/`PUBLIC_HOST`.

5. Reconexiones frecuentes del stream:
   - Inestabilidad de red hacia OpenCTI, o `OPENCTI_URL` apuntando a un host/puerto incorrecto.
   - `hub-service` reconecta solo con backoff (ver `docs/RUNBOOK.md` sección 2); revisar `GET /admin/api/v1/ingestion/status`.

## 11) Seguridad y buenas practicas

- No versionar ni compartir el archivo `.env` con secretos reales (ver `.gitignore`).
- Rotar credenciales expuestas historicamente (tokens, API keys, passwords) — para la clave de cifrado de secretos del Hub ver `docs/RUNBOOK.md` sección 3.
- Restringir acceso al puerto HTTPS publicado (`NGINX_HTTPS_PORT`) solo a equipos autorizados.
- Asegurar permisos de solo lectura para el código montado en `/app` (ya declarado `:ro` en `docker-compose.yml`).
- Monitorear el tamaño del volumen `hub_feeds` y de las bases SQLite en `hub_state`.

## 12) Estado funcional actual

Ver [spec/PROJECT-MAP.md](spec/PROJECT-MAP.md) para el estado componente por componente y sus huecos conocidos documentados, y las secciones 13 a 17 de este README para instalar/probar cada Entrega. El flujo descrito en la sección 6 (OpenCTI stream -> filtro -> TXT -> Nginx) corresponde al script legado `opencti_feed_builder.py`, ya reemplazado por `hub/`.

## 13) Hub - rediseno (Entrega 1)

El paquete `hub/` es el rediseno especificado en [spec/](spec/README.md), separado del script legado (`opencti_feed_builder.py`) y de su configuracion. Cubre la Entrega 0 (contratos) y la Entrega 1 (Nucleo confiable: `spec/09-ROADMAP-ACCEPTANCE.md`) — ver el estado real componente por componente en [spec/PROJECT-MAP.md](spec/PROJECT-MAP.md). El Admin API de Entrega 2 (FastAPI, CRUD de destinos/politicas, adapters) esta documentado por separado en la seccion [14](#14-admin-api---rediseno-entrega-2).

### 13.1 Instalacion

```bash
pip install -r requirements-dev.txt
```

`hub/` tiene su propio archivo de dependencias (`hub/requirements.txt`, `hub/requirements-dev.txt`), separado del script legado; el comando de arriba instala todo (legado + Hub + Admin API) via los `-r` anidados.

### 13.2 Variables de entorno del Hub

Archivo de configuracion propio, separado del `.env` de OpenCTI (spec/02 "Configuracion minima de conexion"; ninguna variable compartida del stack de OpenCTI se lee desde aqui):

| Variable | Obligatoria | Default | Para que sirve |
| --- | --- | --- | --- |
| `OPENCTI_URL` | Si | — | Base URL de OpenCTI |
| `OPENCTI_SERVICE_ACCOUNT_TOKEN` | Si | — | Token del service account dedicado del Hub (nunca `OPENCTI_ADMIN_TOKEN`) |
| `OPENCTI_STREAM_ID` | No | stream general | Live Stream especifico a consumir |
| `OPENCTI_TLS_VERIFY` | No | `true` | `false` solo en desarrollo local |
| `OPENCTI_CA_CERT_PATH` | No | — | CA interna/autofirmada |
| `POLICY_TTL_DAYS` | No | `30` | TTL por defecto usado en `effective_expiration` |
| `BACKFILL_WINDOW_DAYS` / `BACKFILL_MAX_PAGES` / `BACKFILL_PAGE_SIZE` | No | `7` / `10` / `100` | Alcance del backfill inicial y de cada reconciliacion |
| `RECONCILE_INTERVAL_SECONDS` | No | `600` | Cadencia de reconciliacion GraphQL periodica |
| `MAX_SSE_LINE_BYTES` / `MAX_SSE_EVENT_BYTES` | No | `262144` / `2097152` | Limites de tamano SSE (spec/03 "Limites") |
| `TXT_FEED_DIR` / `TXT_FEED_MAX_RECORDS` | No | `./feeds` / `20000` | Carpeta de feeds TXT y capacidad por archivo |
| `HUB_SOURCE_ID` | No | `opencti-main` | Identificador de fuente en el evento canonico y el cursor |
| `HUB_STATE_DIR` | No | `./state` | SQLite de cursor/ledger y heartbeat |

### 13.3 Correr el Hub

```bash
python -m hub.service
python -m hub.service --healthcheck   # exit 0/1 segun heartbeat reciente
```

Apagado ordenado con `Ctrl+C`/`SIGTERM`: termina el evento en curso y cierra (cada evento aceptado hacia un destino `txt_feed` ya reconstruye su archivo al procesarse, `hub/adapters/txt_feed_adapter.py`).

### 13.4 Tests

```bash
pytest
```

La suite de `tests/hub/` cubre los contratos de Entrega 0, el nucleo de Entrega 1 (normalizacion, dedup, TTL, cursor, ledger, SSE, cliente GraphQL, backfill, reconciliacion, escritura TXT) y el Admin API de Entrega 2 (destinos, politicas, adapters, retries/circuit breaker, endpoints FastAPI) contra fixtures, GraphQL simulado y `requests` simulado — no contra una instancia OpenCTI o un destino real (ver limites documentados en spec/PROJECT-MAP.md).

### 13.5 Backup y restore

El estado durable vive en `HUB_STATE_DIR` (`cursor.sqlite3`, `ledger.sqlite3`, `destinations.sqlite3`, `policies.sqlite3`, `tokens.sqlite3`, `idempotency.sqlite3`, `audit.sqlite3`, `ingestion_control.sqlite3`, `.heartbeat`) y los feeds materializados en `TXT_FEED_DIR`. Para respaldar, copiar ambos directorios con el proceso detenido o usando `sqlite3 .backup` sobre los `.sqlite3`; para restaurar, reponer los archivos antes de arrancar `hub.service`/`hub.api` (el estado se recarga solo al iniciar).

## 14) Admin API - rediseno (Entrega 2)

FastAPI en `hub/api/` (spec/09-ROADMAP-ACCEPTANCE.md Entrega 2: "API y primer destino"). Comparte el mismo `HUB_STATE_DIR` que `hub.service` pero es un proceso separado (spec/03 "Admin API: servicio separado del consumidor OpenCTI").

### 14.1 Correr el Admin API

```bash
python -m hub.api
# variables opcionales: HUB_API_HOST (default 0.0.0.0), HUB_API_PORT (default 8000)
```

Docs interactivas en `/admin/api/v1/docs`, OpenAPI en `/admin/api/v1/openapi.json`.

### 14.2 Autenticacion

API tokens locales (spec/08 decision #5, sin OIDC/SSO todavia): un token se genera una sola vez, se persiste solo su hash. No hay endpoint HTTP para crear el primer token (seria un huevo-y-gallina de auth); se genera con un script corto contra el mismo `tokens.sqlite3`:

```bash
python -c "
from hub.api.token_store import init_db, create_token
conn = init_db('/ruta/a/HUB_STATE_DIR/tokens.sqlite3')
token, plaintext = create_token(conn, role='security-admin')
print(plaintext)  # guardarlo ahora: no se puede volver a mostrar
"
```

Roles (spec/08, jerarquicos: uno mayor cubre las acciones de uno menor): `viewer` < `operator` < `policy-admin` < `security-admin`.

### 14.3 Ejemplo: alta de un destino TXT + politica + publicacion

```bash
TOKEN=... # token con rol security-admin/policy-admin segun el paso

curl -s -X POST http://localhost:8000/admin/api/v1/destinations \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"destination_id": "fortigate-prod", "name": "Fortigate", "adapter": "txt_feed",
       "allowed_ioc_types": ["hash/sha256"], "capacity": {"max_records_per_file": 20000}}'

curl -s -X POST http://localhost:8000/admin/api/v1/policies \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"policy_id": "fortigate-policy", "destination_id": "fortigate-prod",
       "allowed_iocs": [{"family": "hash", "subtypes": ["sha256"]}], "ttl_days": {"sha256": 60}}'

curl -s -X POST http://localhost:8000/admin/api/v1/policies/fortigate-policy/publish \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"version": 1}'
```

Un destino sin politica publicada no recibe entregas (spec/08 "politica obligatoria antes de activar destino").

### 14.4 Auditoria, busqueda de eventos y control de ingestion

Preparacion de backend para Entrega 3 (spec/07-ADMIN-UI-ANGULAR.md), antes de tocar Angular:

```bash
# Auditoria (rol viewer), filtrable por actor/accion/recurso/fecha
curl -s "http://localhost:8000/admin/api/v1/audit?resource_type=destination" -H "Authorization: Bearer $TOKEN"

# Buscar entregas en el ledger (rol viewer)
curl -s "http://localhost:8000/admin/api/v1/events?stix_id=indicator--..." -H "Authorization: Bearer $TOKEN"
curl -s "http://localhost:8000/admin/api/v1/events/{event_id}" -H "Authorization: Bearer $TOKEN"  # linea de tiempo

# Control de ingestion: pausar/reanudar y reconciliar (rol operator)
curl -s -X POST http://localhost:8000/admin/api/v1/ingestion/pause -H "Authorization: Bearer $TOKEN"
curl -s -X POST http://localhost:8000/admin/api/v1/ingestion/reconcile -H "Authorization: Bearer $TOKEN"

# Rebobinar cursor (rol security-admin, motivo obligatorio)
curl -s -X POST http://localhost:8000/admin/api/v1/ingestion/rewind \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"cursor_value": "<id-de-evento-sse-anterior>", "reason": "brecha detectada en reconciliacion"}'
```

`hub.service` (ingestion) y `hub.api` (Admin API) son procesos separados: los pedidos de pausar/reconciliar/rebobinar se escriben en `ingestion_control.sqlite3` (`hub/ingestion_control.py`) y el loop de `hub.service` los aplica sondeando esa tabla, no hay llamada directa entre procesos.

### 14.5 Limites conocidos

Ver "Pendiente conocido: Entrega 2" y "Pendiente conocido: Entrega 3 (UI Angular)" en [spec/PROJECT-MAP.md](spec/PROJECT-MAP.md): sin cola/workers real (reintentos son manuales via `POST /deliveries/{id}/retry` o automatizacion externa), adapter HTTP push sin validar contra un destino real, `credential_ref` solo resuelve `env://` (sin secret manager todavia), rate limit en memoria del proceso, sin Canonical Event Store (el Inspector del Event Ledger queda acotado a lo que el ledger ya guarda), sin log store/SSE real (la UI usa polling corto).

## 15) UI Angular - Admin Dashboard (Entrega 3)

Consola operativa en `ui/` (spec/07-ADMIN-UI-ANGULAR.md), consume el Admin API de la seccion 14. Angular **21.x** (no 22.x: Angular CLI 22.x exige Node >=24.15.0/22.22.3/26 y el entorno de build tenia Node 24.13.0 sin gestor de version disponible para subir; sustitucion documentada, ver [CHANGELOG.md](CHANGELOG.md) `[0.1.5]`).

### 15.1 Instalar y correr en desarrollo

```bash
cd ui
npm install
npx ng serve   # http://localhost:4200, apunta a http://localhost:8000 (ver src/environments/environment.development.ts)
```

El Admin API debe correr por separado (seccion 14.1) **con** `ADMIN_UI_ORIGINS=http://localhost:4200` seteado (spec/08 "CORS cerrado a origen de UI": cerrado por defecto, se habilita explicitamente al origen real de la UI):

```bash
ADMIN_UI_ORIGINS=http://localhost:4200 python -m hub.api
```

Login: pegar un API token ya generado (seccion 14.2) y elegir el rol con el que se creo. El token vive solo en memoria de la pestana (sin `localStorage`/`sessionStorage`, spec/07 "Seguridad frontend"): se pierde al refrescar la pagina, por diseno.

### 15.2 Build de produccion

```bash
cd ui
npx ng build
```

Genera `ui/dist/ui/`, para servir detras del mismo Nginx/dominio que expone el Admin API (mismo origen evita configurar `ADMIN_UI_ORIGINS`; `src/environments/environment.ts` asume `apiBaseUrl: '/admin/api/v1'` relativo).

### 15.3 Tests unitarios y E2E

```bash
cd ui
npx ng test --watch=false       # Vitest, unitarios

npx playwright install chromium # una sola vez
npx playwright test             # E2E: levanta backend Python + ng serve automaticamente
```

El E2E (`ui/e2e/`) siembra un token `security-admin` y una entrega en dead-letter en un `state_dir` temporal que se limpia al inicio de cada corrida (para que sea reproducible), y no depende de una instancia OpenCTI real (`OPENCTI_URL`/`OPENCTI_SERVICE_ACCOUNT_TOKEN` ficticios, suficientes para levantar el proceso). Cubre: login (redirect sin sesion, token valido, token invalido), descartar una entrega de DLQ con motivo obligatorio, y el flujo completo de politicas (crear borrador -> simular con el error esperado sin OpenCTI real -> publicar con motivo).

### 15.4 Secciones de navegacion

Las 7 de spec/07: Overview, Observabilidad & Logs, Operaciones & DLQ, Politicas, Destinos, OpenCTI/Ingesta, Auditoria & Configuracion. RBAC client-side (misma jerarquia que la seccion 14.2) oculta botones de acciones que igual fallarian con 403 en el servidor -- la autorizacion real siempre la aplica el Admin API.

### 15.5 Limites conocidos

Ver "Pendiente conocido: Entrega 3 (UI Angular)" en [spec/PROJECT-MAP.md](spec/PROJECT-MAP.md): sin visor de logs en vivo (no hay log store), sin configuracion operativa editable (no hay endpoints), sin preview de volumen al rebobinar el cursor -- ambos gaps se muestran explicitamente como "no disponible" en la propia UI en vez de simularse.

## 16) Integraciones - Entrega 4

spec/09-ROADMAP-ACCEPTANCE.md "Entrega 4": adapters de destino nuevos priorizados por esfuerzo real (spec/05 "Modos de entrega y esfuerzo relativo"), servidor TAXII 2.1 propio y alertas email/webhook.

### 16.1 Bajo esfuerzo (reusan el adapter `txt_feed` existente, sin codigo nuevo)

Fortinet/FortiGate (External Connector), Palo Alto PAN-OS (External Dynamic List), pfSense/pfBlockerNG, Cisco Security Intelligence: los cuatro consumen un feed TXT plano (un IOC por linea, feeds separados por subtipo) -- exactamente lo que `adapter: "txt_feed"` ya escribe desde la Entrega 2. Alta vía `POST /admin/api/v1/destinations` con `"adapter": "txt_feed"`, sin campos adicionales.

Recorda la escalera de autenticacion cuando el fabricante hace *poll* sobre la URL del Hub (spec/05, nunca publicar sin al menos un control de acceso): Basic Auth cuando el fabricante lo soporta (Fortinet, Palo Alto), token no adivinable en la URL cuando no (Cisco Security Intelligence, pfBlockerNG), o mTLS si ya se gestiona PKI interna (Palo Alto).

### 16.2 Esfuerzo medio (adapters nuevos, formato propio o API dedicada)

```bash
TOKEN=... # rol security-admin

# Check Point (CSV multi-columna, columnas configurables)
curl -s -X POST http://localhost:8000/admin/api/v1/destinations \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"destination_id": "checkpoint-prod", "name": "Check Point", "adapter": "csv_feed", "format": "csv",
       "format_options": {"columns": ["family", "subtype", "value", "score", "confidence", "marking", "created_at", "valid_until"]}}'

# MikroTik RouterOS (.rsc, solo IP/CIDR -- address-list no acepta dominios ni hashes)
curl -s -X POST http://localhost:8000/admin/api/v1/destinations \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"destination_id": "mikrotik-prod", "name": "MikroTik", "adapter": "mikrotik_rsc", "format": "rsc",
       "allowed_ioc_types": ["network/ipv4", "network/cidr"], "format_options": {"list_name": "hub-blocklist"}}'

# Wazuh (CDB list -- el Hub solo materializa el archivo; sincronizarlo al manager y recargar es responsabilidad externa)
curl -s -X POST http://localhost:8000/admin/api/v1/destinations \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"destination_id": "wazuh-prod", "name": "Wazuh", "adapter": "wazuh_cdb", "format": "cdb"}'

# QRadar (Reference Set API, bulk_load)
curl -s -X POST http://localhost:8000/admin/api/v1/destinations \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"destination_id": "qradar-refset", "name": "QRadar", "adapter": "qradar_reference_set", "format": "json",
       "endpoint": "https://qradar.example", "credential_ref": "env://QRADAR_SEC_TOKEN",
       "format_options": {"reference_set_name": "hub-malicious-ips"}}'
```

MikroTik: el Hub solo genera el bloque `add address=... list=... timeout=...`; envolverlo en el propio Scheduler/`/tool fetch` del router queda del lado del operador (spec/05 no documenta una plantilla `.rsc` completa). Wazuh: el archivo CDB generado en `TXT_FEED_DIR/<destino>/<subtipo>.cdb` debe sincronizarse al filesystem del manager y disparar el reload por fuera del Hub (Decision #11 de spec/09, resuelta asi para no construir un agente/sync-companion con su propia superficie de credenciales SSH).

### 16.3 Alto esfuerzo: STIX 2.1 y servidor TAXII 2.1

`stix_bundle_feed` materializa un `bundle.json` por destino (Bundle STIX 2.1 con todos los indicators vigentes). `taxii2` expone esos mismos indicators via un servidor TAXII 2.1 minimo y de solo lectura que corre el propio Hub (Decision #4 de spec/09: el Hub filtra por politica antes de exponer el IOC, por eso no alcanza con el TAXII nativo de OpenCTI para este caso):

```bash
curl -s -X POST http://localhost:8000/admin/api/v1/destinations \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"destination_id": "cisco-tid", "name": "Cisco TID", "adapter": "taxii2", "format": "stix2.1",
       "credential_ref": "env://TAXII_CISCO_BASIC_AUTH"}'
```

`env://TAXII_CISCO_BASIC_AUTH` debe contener `usuario:password` (mismo secreto resuelto por `hub/credentials.py`, formato Basic Auth). Consumo (Cisco TID u otro cliente TAXII 2.1):

```bash
curl -s http://localhost:8000/taxii2/                                          # discovery, publico
curl -s http://localhost:8000/taxii2/hub/collections/                          # lista, publico (solo metadata)
curl -s -u cisco:s3cret http://localhost:8000/taxii2/hub/collections/cisco-tid/objects/
```

El Hub es productor: `POST` a `.../objects/` devuelve 405. Una coleccion TAXII es append/update-only -- descartar/revocar un IOC republica el mismo objeto con `revoked: true`, nunca lo borra (a diferencia de `stix_bundle_feed`, que si remueve el objeto del bundle porque es una foto del estado actual, no un log).

### 16.4 Alertas email/webhook

```bash
# Variables de entorno relevantes (todas opcionales; sin ninguna, no se notifica por ningun canal)
ALERT_COOLDOWN_SECONDS=300                  # minimo entre notificaciones repetidas de la misma alerta
ALERT_SMTP_HOST=smtp.example.internal
ALERT_SMTP_PORT=587
ALERT_EMAIL_FROM=hub@example.internal
ALERT_EMAIL_TO=soc@example.internal,ops@example.internal
ALERT_EMAIL_CREDENTIAL_REF=env://ALERT_SMTP_CREDENTIALS   # "usuario:password"
ALERT_EMAIL_MIN_SEVERITY=warning            # info | warning | critical
ALERT_WEBHOOK_URL=https://ops.example/hooks/hub-alerts
ALERT_WEBHOOK_CREDENTIAL_REF=env://ALERT_WEBHOOK_SECRET   # secreto HMAC-SHA256
ALERT_WEBHOOK_MIN_SEVERITY=info

# Disparar evaluacion manualmente (o via cron externo -- sin scheduler real, spec/03 "Queue y workers")
curl -s -X POST http://localhost:8000/admin/api/v1/alerts/evaluate -H "Authorization: Bearer $TOKEN"
curl -s http://localhost:8000/admin/api/v1/alerts -H "Authorization: Bearer $TOKEN"
curl -s -X POST http://localhost:8000/admin/api/v1/alerts/{alert_id}/acknowledge -H "Authorization: Bearer $TOKEN"
```

`hub.service` (el proceso de ingestion) ademas evalua automaticamente, una vez por minuto en su propio loop, las 2 condiciones que dependen de su estado in-process (OpenCTI desconectado, cursor sin avanzar); el resto (dead-letter, destino sin entrega, feed sin rebuild) se evaluan desde el Admin API porque es ese proceso el que tiene ledger/destinos/feeds a mano.

### 16.5 Limites conocidos

Ver "Pendiente conocido: Entrega 4 (Integraciones)" en [spec/PROJECT-MAP.md](spec/PROJECT-MAP.md): sin archivo historico de IOC (fuera de alcance de esta entrega), sin cola/workers real, SLO de latencia por destino sigue como umbral unico a nivel Hub (no por destino), 6 de las 11 condiciones de alerta de spec/06 sin señal disponible (espacio en disco, TLS invalido, caida de volumen historico, entre otras), QRadar y TAXII sin validar contra una instancia/consumidor real, y Check Point/MikroTik/Wazuh sin confirmar el esquema exacto que espera el parser real de cada fabricante (spec/05 lo deja como decision de implementacion).

## 17) Produccion - Entrega 5

spec/09-ROADMAP-ACCEPTANCE.md "Entrega 5": PostgreSQL, OIDC/SSO y RBAC, secret manager, OpenTelemetry Collector, endurecimiento de produccion (load test, chaos/recovery, backup/restore, rotacion). Decisiones fijadas: PostgreSQL solo se documenta el camino de migracion (sin codigo esta pasada, ver spec/PROJECT-MAP.md "Pendiente conocido: Entrega 5"); OIDC generico probado contra un IdP simulado, no uno real; secret manager como cifrado en reposo, no una integracion externa.

### 17.1 Secret manager (cifrado en reposo)

```bash
# Generar una clave nueva (una sola vez, guardarla fuera del repo/backups)
python -c "from hub.secret_encryption import SecretCipher; print(SecretCipher.generate_key())"

# Variables de entorno (una de las dos, nunca ambas necesarias)
SECRET_ENCRYPTION_KEY=<clave-generada-arriba>
SECRET_ENCRYPTION_KEY_FILE=/ruta/a/un/archivo/con/la/clave   # alternativa, ej. secret montado por el orquestador

TOKEN=... # rol security-admin

# Guardar un secreto (el valor nunca se devuelve ni se audita despues de este POST)
curl -s -X POST http://localhost:8000/admin/api/v1/secrets \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"name": "qradar-token", "value": "el-token-real-de-qradar"}'

curl -s http://localhost:8000/admin/api/v1/secrets -H "Authorization: Bearer $TOKEN"           # solo nombres
curl -s -X POST http://localhost:8000/admin/api/v1/secrets/qradar-token/test -H "Authorization: Bearer $TOKEN"  # descifra y confirma, sin exponer el valor
curl -s -X DELETE http://localhost:8000/admin/api/v1/secrets/qradar-token -H "Authorization: Bearer $TOKEN"
```

Cualquier `credential_ref` (destinos, alertas, el `client_secret` de OIDC) acepta `secret://<name>` ademas de `env://NAME` de siempre -- mismo punto unico de resolucion (`hub/credentials.py`). Rotacion de la clave: ver `docs/RUNBOOK.md` seccion 3 (`POST /admin/api/v1/secrets/rotate-key`).

### 17.2 OIDC/SSO

```bash
OIDC_ISSUER_URL=https://idp.example.com/realms/hub          # IdP con descubrimiento OIDC estandar
OIDC_CLIENT_ID=hub-admin-ui
OIDC_CLIENT_SECRET_REF=secret://oidc-client-secret            # o env://OIDC_CLIENT_SECRET
OIDC_REDIRECT_URI=https://hub.example.com/admin/api/v1/auth/oidc/callback
OIDC_ROLE_CLAIM=roles                                          # nombre del claim del ID token con el/los rol(es)
OIDC_ROLE_MAPPING={"hub-security-admins": "security-admin", "hub-operators": "operator"}
OIDC_SESSION_TTL_SECONDS=28800
```

La UI muestra un boton "Entrar con SSO" en `/login` (navegacion de pagina completa a `/admin/api/v1/auth/oidc/login`, no una ruta Angular -- el redirect a un IdP externo y de vuelta necesita una recarga real del navegador). La sesion viaja en una cookie `hub_session` HttpOnly+Secure+SameSite=Lax (nunca visible a JS, a diferencia del token pegado de Entrega 2 que vive en memoria); `GET /admin/api/v1/auth/whoami` la detecta al arrancar la app. `POST /admin/api/v1/auth/logout` la revoca. Los API tokens de Entrega 2 (`README.md §14.2`) siguen funcionando sin cambios para automatizacion.

**No validado contra un IdP real** (Keycloak/Okta/Azure AD) -- los tests usan un IdP simulado con un keypair RSA propio. Antes de un despliegue real, probar el flujo completo contra el IdP corporativo elegido.

### 17.3 OpenTelemetry

```bash
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318   # sin esto, no se instala ningun exporter (aditivo/opcional)
OTEL_SERVICE_NAME=opencti-ioc-hub
```

Config de ejemplo del Collector en [deploy/otel-collector-config.yaml](deploy/otel-collector-config.yaml) (incluye un processor que filtra atributos con pinta de secreto antes de exportar). Los 7 spans de spec/06 seccion 4 (`opencti.stream.receive`, `opencti.event.normalize`, `policy.evaluate`, `delivery.render`/`.send`/`.acknowledge`, `feed.rebuild`) ya estan instrumentados en el codigo -- sin `OTEL_EXPORTER_OTLP_ENDPOINT` configurado, el Hub sigue funcionando igual (spec/06: "Prometheus, OpenTelemetry... quedan como salidas opcionales").

### 17.4 Endurecimiento de produccion

```bash
# Backup / restore -- ver docs/RUNBOOK.md para el procedimiento completo
python scripts/backup_state.py --state-dir "$HUB_STATE_DIR" --feed-dir "$TXT_FEED_DIR" --out-dir ./backups
python scripts/restore_state.py hub-backup-20260814T120000Z.tar.gz --state-dir ./state --feed-dir ./feeds

# Load test simple (sin dependencia nueva)
python scripts/load_test.py --base-url http://localhost:8000/admin/api/v1 \
  --token "$TOKEN" --endpoint /destinations --requests 200 --concurrency 20
```

Ver [docs/RUNBOOK.md](docs/RUNBOOK.md) para el procedimiento completo (backup/restore con prueba mensual, recuperacion ante caida de OpenCTI, rotacion de la clave de secretos, recuperacion de dead-letter). Caida de OpenCTI cubierta por un chaos test (`tests/hub/test_chaos_opencti_outage.py`) contra el backoff/reconexion ya construido desde Entrega 1.

### 17.5 Migracion a PostgreSQL (solo documentada, sin construir)

Ver "Pendiente conocido: Entrega 5" en [spec/PROJECT-MAP.md](spec/PROJECT-MAP.md): ruta recomendada, orden sugerido de migracion de los 12 modulos `hub/*_store.py`, y el disparador documentado en spec/06 ("PostgreSQL para produccion multi-worker"). Sin driver ni codigo de conexion Postgres en el repo todavia.

### 17.6 Limites conocidos

Ver "Pendiente conocido: Entrega 5 (Producción)" en [spec/PROJECT-MAP.md](spec/PROJECT-MAP.md): PostgreSQL sin construir (solo documentado), sin integracion con un secret manager externo real, OIDC sin validar contra un IdP real, chaos test acotado a la caida de OpenCTI (no destino/DB), load test simple no una herramienta real tipo k6/locust, archivo historico de IOC y SLO de latencia por destino siguen sin resolver.
