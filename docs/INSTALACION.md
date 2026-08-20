# Instalación y puesta en marcha

Ver también: [README.md](../README.md) para la idea general, [docs/ESTADO.md](ESTADO.md) para qué está probado y qué no.

## 1) Infraestructura y sistema

- Docker Engine + Docker Compose.
- Por defecto todo se resuelve relativo a este repo, sin tocar nada: el código (`hub/`) desde `.`, el build de `ng build` desde `./ui/dist/ui/browser`, `nginx.conf` desde `./nginx.conf` y los certificados TLS desde `./certs/`. Solo hace falta declarar `HUB_APP_PATH`/`UI_DIST_PATH`/`NGINX_CONF_PATH`/`NGINX_CERTS_PATH` en `.env` si tu despliegue guarda alguna de esas carpetas en otro lugar (ver `.env.example`).
- El Hub arranca standalone: no necesita conocer OpenCTI para levantar el stack (`docker compose up -d` no requiere ninguna variable `OPENCTI_*`, ni compartir red Docker con OpenCTI). La conexión se configura después, en caliente, desde la consola web — ver sección 3 y 4.

## 2) Requisitos de OpenCTI

- OpenCTI arriba y saludable, gestionado por fuera de este repositorio, alcanzable por HTTPS desde donde corre `hub-service`/`hub-api` (no hace falta estar en la misma red Docker: es una llamada saliente normal).
- Token de cuenta de servicio (no administrativa) con permisos de stream y GraphQL — ver [docs/RUNBOOK.md](RUNBOOK.md) §7 (crear y configurar el Live Stream correctamente, incluyendo el filtro de tipos y por qué no usar el token del admin). Se carga desde la consola web (pantalla "Conexión OpenCTI") o vía `PUT /admin/api/v1/opencti-settings`, no desde `.env`.

## 3) Requisitos de Nginx

- `nginx.conf` es una plantilla: `server_name`, la redirección HTTP→HTTPS y las rutas de certificado usan `${PUBLIC_HOST}`/`${NGINX_HTTPS_PORT}`, resueltos con `envsubst` al arrancar el contenedor (ver servicio `nginx` en `docker-compose.yml`). No hardcodear IPs/hosts en este archivo.
- Certificados presentes, con nombre de archivo igual a `PUBLIC_HOST` (variable en `.env`):
  - `/etc/nginx/certs/$PUBLIC_HOST.crt`
  - `/etc/nginx/certs/$PUBLIC_HOST.key`
- Puerto HTTPS publicado (`NGINX_HTTPS_PORT` en `.env`, `8446` por defecto).

## 4) Puesta en marcha

1. Copiar `.env.example` a `.env` y completar `PUBLIC_HOST` (y `NGINX_HTTPS_PORT` si el default 8446 no está libre). No hace falta nada de OpenCTI todavía — el Hub arranca standalone.
2. Verificar que las rutas de host usadas en volúmenes existan y tengan permisos (o dejar los defaults relativos al repo, ver sección 1).
3. Levantar el Hub:

```bash
docker compose up -d
```

4. Validar estado (los 3 contenedores deben quedar `Up`/`healthy` aunque OpenCTI todavía no esté configurado):

```bash
docker compose ps
docker compose logs -f hub-service
docker compose logs -f hub-api
docker compose logs -f nginx
```

5. Generar el primer token de Admin API ([docs/API-ADMIN.md](API-ADMIN.md) §2) y, con él, configurar la conexión a OpenCTI desde la consola web (pantalla "Conexión OpenCTI") o directo por API ([docs/API-ADMIN.md](API-ADMIN.md) §3):

```bash
curl -s -X PUT https://$PUBLIC_HOST:$NGINX_HTTPS_PORT/admin/api/v1/opencti-settings -k \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"url": "https://opencti.example.local:8443", "token": "<token-de-cuenta-de-servicio>"}'
```

Recién en este punto `hub-service` deja de esperar y arranca el backfill + Live Stream.

6. Probar la Admin API y los feeds desde red interna:

```bash
curl -k https://$PUBLIC_HOST:$NGINX_HTTPS_PORT/healthz/liveness
curl -k https://$PUBLIC_HOST:$NGINX_HTTPS_PORT/feeds/<destino>/<subtipo>.txt
```

## 5) Respaldo de `docker-compose.yml`

En este repositorio hay dos piezas relacionadas:

- `docker-compose.yml`: archivo principal — `hub-service`, `hub-api` y `nginx`.
- `feed-builder.yml`: respaldo/plantilla de `hub-service`/`hub-api` (no incluye `nginx`), para recuperar rápido esos dos servicios si el compose principal se pierde: `docker compose -f feed-builder.yml up -d`.

Práctica recomendada: mantener ambos sincronizados cuando se cambian variables, volúmenes o el `command` de `hub-service`/`hub-api`.
