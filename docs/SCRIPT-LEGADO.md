# Script legado: `opencti_feed_builder.py`

> **Esto es referencia histórica, no documentación de uso.** `opencti_feed_builder.py` fue la primera versión de este proyecto: un único script que hacía todo (ingesta, filtrado, escritura de feeds). Ya fue reemplazado por `hub/` (ver el resto de `docs/`) y **no se despliega desde `docker-compose.yml`**. El archivo se mantiene en el repo solo para no perder el historial de cómo funcionaba.

## 1) Inicialización

- Crea `FEEDS_DIR` si no existe.
- Inicializa cabeceras de `ip.txt`, `url.txt` y `hash.txt`.
- Carga estado desde `state.json`:
  - `seen_ids`: IDs STIX ya vistos (deduplicación por ID).
  - `seen_values`: IOC ya vistos con timestamp de expiración.
  - `pending`: cola por tipo (ip/url/hash) para modo batch.

Si detecta migración de formato de estado, limpia y reconstruye feeds para evitar IOC expirados heredados.

## 2) Consumo del stream

- Se conecta a:
  - `OPENCTI_URL/stream/{STREAM_ID}` si `STREAM_ID` viene definido.
  - `OPENCTI_URL/stream` en caso contrario.
- Procesa eventos SSE acumulando líneas `data:` hasta línea vacía (fin de evento).

## 3) Extracción y clasificación de IOC

Para cada evento:

1. Intenta parsear JSON.
2. Normaliza payload (dict o JSON embebido en string).
3. Extrae STIX desde `payload.data.data`, `payload.data` o `payload` directo.
4. Si es indicator:
   - Extrae `observable_type` y valor desde extensión/patrón/nombre.
   - Clasifica como ip/url/hash.
5. Si no es indicator y `PROCESS_OBSERVABLE_HASHES=true`:
   - Busca hashes SHA-256 en observables y también en cualquier texto del payload (si `HASH_FROM_ANY_EVENT=true`).

## 4) Filtros aplicados

Filtros base (todos los tipos):

- `revoked != true`
- `valid_until` no expirado (si existe)
- TLP permitido por `ALLOW_TLP` (si no hay label TLP, pasa)
- TTL máximo por tipo: `MAX_AGE_DAYS_IP`, `MAX_AGE_DAYS_URL`, `MAX_AGE_DAYS_HASH`

Filtros de calidad (principalmente indicator):

- `score >= MIN_SCORE`
- `confidence >= MIN_CONFIDENCE`
- `detection` obligatorio solo si `REQUIRE_DETECTION=true`

Excepción para hashes: si `HASH_RELAX_FILTERS=true`, los hashes de indicator usan solo filtros base (más flexible para no perder hashes útiles).

## 5) Escritura de feeds

Dos modos:

- **Modo inmediato** (`WRITE_INTERVAL_SEC <= 0`): cada IOC válido actualiza estado y dispara rebuild del archivo de su feed.
- **Modo batch** (`WRITE_INTERVAL_SEC > 0`): encola IOC en `pending` y hace flush periódico. En cada flush, actualiza `seen_values` y reconstruye feeds.

La reconstrucción deja solo IOC vigentes (no expirados).

## 6) Backfill inicial

Si `BACKFILL_ENABLED=true`, consulta GraphQL de OpenCTI para recorrer indicadores recientes y extraer hashes históricos dentro de la ventana `BACKFILL_DAYS`, con paginación controlada por `BACKFILL_PAGE_SIZE` y `BACKFILL_MAX_PAGES`.

## 7) Persistencia y compactación

- Guarda `state.json` de forma atómica (tmp + replace).
- Purga IOC expirados de memoria.
- Reintenta conexión SSE con backoff exponencial ante error.

## 8) Variables de configuración

### Realmente usadas por el script

`OPENCTI_URL`, `OPENCTI_TOKEN`, `STREAM_ID`, `FEEDS_DIR`, `MIN_SCORE`, `MIN_CONFIDENCE`, `ALLOW_TLP`, `REQUIRE_DETECTION`, `DEBUG_DUMP_ONCE`, `STATS_EVERY_SEC`, `PROCESS_OBSERVABLE_HASHES`, `WRITE_INTERVAL_SEC`, `HASH_FROM_ANY_EVENT`, `HASH_ONLY_SHA256`, `HASH_RELAX_FILTERS`, `BACKFILL_ENABLED`, `BACKFILL_DAYS`, `BACKFILL_PAGE_SIZE`, `BACKFILL_MAX_PAGES`, `PUBLIC_FEEDS_BASE_URL`, `MAX_AGE_DAYS_IP`, `MAX_AGE_DAYS_URL`, `MAX_AGE_DAYS_HASH`.

### Declaradas en su momento pero nunca implementadas en el script

`MAX_RECORDS_PER_FEED`, `URL_STRIP_SCHEME`, `URL_KEEP_QUERY`.

Nota: existía contexto funcional de un límite de 20.000 eventos/IOC en la presentación original de la herramienta, pero ese límite nunca se aplicó explícitamente con `MAX_RECORDS_PER_FEED` en este script — el control real de crecimiento dependía de TTL + deduplicación + compactación/rebuild. (El reemplazo, `hub/`, sí implementa ese límite por destino — ver [docs/API-ADMIN.md](API-ADMIN.md).)

## 9) Ajustes que se tocaban normalmente

- Umbral de calidad: `MIN_SCORE`, `MIN_CONFIDENCE`, `REQUIRE_DETECTION`.
- Compartición TLP: `ALLOW_TLP`.
- Retención/vida útil por tipo: `MAX_AGE_DAYS_IP`, `MAX_AGE_DAYS_URL`, `MAX_AGE_DAYS_HASH`.
- Rendimiento y latencia de escritura: `WRITE_INTERVAL_SEC`.
- Alcance de relleno histórico: `BACKFILL_ENABLED`, `BACKFILL_DAYS`, `BACKFILL_PAGE_SIZE`, `BACKFILL_MAX_PAGES`.
