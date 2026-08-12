# Feed Builder para OpenCTI

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

- https://10.0.100.13:8446/feeds/ip.txt
- https://10.0.100.13:8446/feeds/url.txt
- https://10.0.100.13:8446/feeds/hash.txt

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

- Certificados presentes segun nginx.conf:
  - /etc/nginx/certs/10.0.100.13.crt
  - /etc/nginx/certs/10.0.100.13.key
- Puerto HTTPS publicado (8446 segun .env actual).

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
curl -k https://10.0.100.13:8446/feeds/ip.txt
curl -k https://10.0.100.13:8446/feeds/url.txt
curl -k https://10.0.100.13:8446/feeds/hash.txt
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
- Certificados TLS y server_name en nginx.conf.
- URL publica de referencia en logs (PUBLIC_FEEDS_BASE_URL).

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
