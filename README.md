# Feed Builder para OpenCTI

**Autor:** Athan Espinoza

> Historial de cambios por versión en [CHANGELOG.md](CHANGELOG.md). Versión actual del Hub: `hub.__version__` (ver [hub/\_\_init\_\_.py](hub/__init__.py)).
>
> Este README documenta el script legado `opencti_feed_builder.py`, hoy en producción. El rediseño en curso (`hub/`, "OpenCTI IOC Distribution Hub") sigue la especificación modular en [spec/](spec/README.md); ver la sección [13) Hub - rediseno (Entrega 1)](#13-hub---rediseno-entrega-1) más abajo para instalarlo y probarlo.

## 1) Objetivo del proyecto

Este proyecto implementa un servicio llamado Feed Builder integrado a OpenCTI para automatizar la publicacion de indicadores de compromiso (IOC) consumibles por firewalls y otras herramientas de seguridad.

El servicio:

- Se conecta al Live Stream (SSE) de OpenCTI.
- Filtra indicadores por calidad (score, confianza, TLP, vigencia y TTL).
- Genera feeds en texto plano (un IOC por linea):
  - ip.txt
  - url.txt
  - hash.txt
- Publica los feeds por HTTPS a traves de Nginx.

Rutas esperadas de consumo:

- https://opencti.example.local:8446/feeds/ip.txt
- https://opencti.example.local:8446/feeds/url.txt
- https://opencti.example.local:8446/feeds/hash.txt

## 2) Que contiene este repositorio

- docker-compose.yml
  - Stack principal OpenCTI + conectores + Nginx + feed-builder.
- feed-builder.yml
  - Respaldo/plantilla del servicio feed-builder (bloque standalone para referencia o recuperacion).
- nginx.conf
  - Reverse proxy HTTPS para OpenCTI y publicacion de /feeds/*.txt.
- opencti_feed_builder.py
  - Script Python que consume stream, filtra IOC y construye feeds.

## 3) Arquitectura y flujo

1. OpenCTI recibe/normaliza inteligencia desde conectores.
2. feed-builder escucha eventos SSE desde OpenCTI (/stream o /stream/{id}).
3. El script interpreta eventos STIX (indicator y observables).
4. Aplica politicas de filtrado.
5. Encola o escribe IOC en feeds segun modo (batch/inmediato).
6. Nginx sirve /feeds/ip.txt, /feeds/url.txt y /feeds/hash.txt por HTTPS.

## 4) Requisitos para funcionar

## 4.1 Infraestructura y sistema

- Docker Engine + Docker Compose.
- Volumenes/rutas de host disponibles para:
  - /opt/opencti/feed-builder (codigo Python)
  - /opt/opencti/feeds (salida de feeds y state.json)
  - /opt/opencti/nginx.conf
  - /opt/opencti/certs (certificado y llave TLS)
- Acceso de red entre contenedores en la red opencti-net.

## 4.2 Requisitos de OpenCTI

- OpenCTI arriba y saludable.
- Token valido de OpenCTI con permisos para stream y GraphQL.
- Servicio opencti resolvible como http://opencti:8080 dentro de la red Docker.

## 4.3 Requisitos de Nginx

- `nginx.conf` es una plantilla: `server_name`, la redirección HTTP->HTTPS y las rutas de certificado usan `${PUBLIC_HOST}`/`${NGINX_HTTPS_PORT}`, resueltos con `envsubst` al arrancar el contenedor (ver servicio `nginx` en `docker-compose.yml`). No hardcodear IPs/hosts en este archivo.
- Certificados presentes, con nombre de archivo igual a `PUBLIC_HOST` (variable en `.env`):
  - /etc/nginx/certs/\$PUBLIC_HOST.crt
  - /etc/nginx/certs/\$PUBLIC_HOST.key
- Puerto HTTPS publicado (`NGINX_HTTPS_PORT` en `.env`, 8446 por defecto).

## 5) Puesta en marcha

1. Revisar variables en .env (credenciales, rutas, limites de recursos, token OpenCTI).
2. Verificar que las rutas de host usadas en volumenes existan y tengan permisos.
3. Levantar stack:

```bash
docker compose up -d
```

4. Validar estado:

```bash
docker compose ps
docker compose logs -f feed-builder
docker compose logs -f nginx
```

5. Probar feeds desde red interna:

```bash
curl -k https://opencti.example.local:8446/feeds/ip.txt
curl -k https://opencti.example.local:8446/feeds/url.txt
curl -k https://opencti.example.local:8446/feeds/hash.txt
```

## 6) Como funciona el script opencti_feed_builder.py

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

- docker-compose.yml:
  - Archivo principal y completo del entorno.
- feed-builder.yml:
  - Respaldo/plantilla del servicio feed-builder.
  - Sirve para recuperar rapidamente el bloque del servicio en caso de cambios o perdida en el compose principal.

Practica recomendada:

- Mantener ambos sincronizados cuando se cambian variables, volumenes o command del feed-builder.

## 10) Operacion y troubleshooting

## 10.1 Comandos utiles

```bash
docker compose logs -f feed-builder
docker compose logs -f nginx
docker compose restart feed-builder
docker compose restart nginx
```

## 10.2 Problemas comunes

1. 401 Unauthorized en stream:
   - Token OPENCTI_TOKEN invalido o sin permisos.

2. Feeds vacios:
   - No hay eventos que cumplan filtros.
   - Umbrales demasiado altos (score/confidence).
   - TLP no permitido.
   - TTL demasiado estricto.

3. Nginx responde 404 en /feeds/*:
   - Volumen de feeds mal montado.
   - FEEDS_DIR del contenedor y path servido por Nginx no coinciden.

4. Problemas TLS:
   - Certificado/llave no encontrados o no coinciden con server_name/IP.

5. Reconexiones frecuentes del stream:
   - Inestabilidad de red o de OpenCTI.
   - Revisar salud de opencti y latencia entre contenedores.

## 11) Seguridad y buenas practicas

- No versionar ni compartir el archivo .env con secretos reales.
- Rotar credenciales expuestas historicamente (tokens, API keys, passwords).
- Restringir acceso a puerto 8446 solo a equipos autorizados.
- Asegurar permisos de solo lectura para codigo montado en /app cuando aplique.
- Monitorear tamano de /opt/opencti/feeds y crecimiento de state.json.

## 12) Estado funcional actual

- Implementacion en marcha blanca (desarrollo personalizado).
- Flujo end-to-end operativo: OpenCTI stream -> filtro -> txt -> Nginx HTTPS.
- Hay espacio de mejora para alinear completamente el comportamiento con algunas variables de compose no usadas todavia (MAX_RECORDS_PER_FEED, URL_STRIP_SCHEME, URL_KEEP_QUERY).

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

El estado durable vive en `HUB_STATE_DIR` (`cursor.sqlite3`, `ledger.sqlite3`, `destinations.sqlite3`, `policies.sqlite3`, `tokens.sqlite3`, `idempotency.sqlite3`, `.heartbeat`) y los feeds materializados en `TXT_FEED_DIR`. Para respaldar, copiar ambos directorios con el proceso detenido o usando `sqlite3 .backup` sobre los `.sqlite3`; para restaurar, reponer los archivos antes de arrancar `hub.service`/`hub.api` (el estado se recarga solo al iniciar).

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

### 14.4 Limites conocidos

Ver "Pendiente conocido: Entrega 2" en [spec/PROJECT-MAP.md](spec/PROJECT-MAP.md): sin cola/workers real (reintentos son manuales via `POST /deliveries/{id}/retry` o automatizacion externa), adapter HTTP push sin validar contra un destino real, `credential_ref` solo resuelve `env://` (sin secret manager todavia), rate limit en memoria del proceso.
