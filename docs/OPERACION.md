# Operación y troubleshooting

Ver también: [docs/RUNBOOK.md](RUNBOOK.md) para procedimientos de recuperación más largos (backup/restore, caída de OpenCTI, rotación de claves).

## 1) Comandos útiles

`hub-service`/`hub-api` corren `python -m hub...` directo contra el código montado (bind mount, sin `--reload`): un cambio en un archivo bajo `hub/` queda en disco al instante, pero el proceso ya arrancado se queda con lo que tenía cargado en memoria hasta que se reinicia. `docker compose up -d` NO reinicia un contenedor que ya está corriendo si no cambió la config del compose — no alcanza para que un cambio de código tenga efecto. Regla simple para no tener que pensar cuál servicio le toca a cada cambio: después de tocar algo en `hub/` (o de correr `ng build` en la UI), reiniciar los tres:

```bash
docker compose restart
```

```bash
docker compose logs -f hub-service
docker compose logs -f hub-api
docker compose logs -f nginx
```

## 2) Problemas comunes

1. **`hub-service` arriba pero nunca hace backfill/conecta al stream:**
   OpenCTI todavía no fue configurado desde la consola web/API: `GET /admin/api/v1/opencti-settings` devuelve `has_token: false` o `url: null` (ver [docs/INSTALACION.md](INSTALACION.md) §4 paso 5). Es el estado esperado en un Hub recién levantado, no un error.

2. **401 en el stream de OpenCTI:**
   El token guardado en "Conexión OpenCTI" es inválido, fue revocado, o no tiene permisos de stream/GraphQL. Probar con `POST /admin/api/v1/opencti-settings/test`.

3. **Feeds vacíos:**
   - No hay eventos que cumplan la política publicada del destino (ver [docs/API-ADMIN.md](API-ADMIN.md)).
   - No hay ninguna política publicada para ese destino: sin política publicada, el destino no recibe entregas.

4. **Nginx responde 404 en `/feeds/*`:**
   Volumen `hub_feeds` mal montado, o el destino/subtipo pedido todavía no generó ningún archivo.

5. **Problemas TLS:**
   Certificado/llave no encontrados o no coinciden con `server_name`/`PUBLIC_HOST`.

6. **Reconexiones frecuentes del stream:**
   Inestabilidad de red hacia OpenCTI, o la URL guardada en "Conexión OpenCTI" apuntando a un host/puerto incorrecto (`POST /admin/api/v1/opencti-settings/test` para confirmar). `hub-service` reconecta solo con backoff (ver [docs/RUNBOOK.md](RUNBOOK.md) §2); revisar `GET /admin/api/v1/ingestion/status`.

## 3) Seguridad y buenas prácticas

- No versionar ni compartir el archivo `.env` con secretos reales (ver `.gitignore`).
- Rotar credenciales expuestas históricamente (tokens, API keys, passwords) — para la clave de cifrado de secretos del Hub ver [docs/RUNBOOK.md](RUNBOOK.md) §3.
- Restringir acceso al puerto HTTPS publicado (`NGINX_HTTPS_PORT`) solo a equipos autorizados.
- Asegurar permisos de solo lectura para el código montado en `/app` (ya declarado `:ro` en `docker-compose.yml`).
- Monitorear el tamaño del volumen `hub_feeds` y de las bases SQLite en `hub_state`.
