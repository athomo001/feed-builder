# Producción: secretos, SSO, trazas y endurecimiento

## 1) Secret manager (cifrado en reposo)

```bash
# Generar una clave nueva (una sola vez, guardarla fuera del repo/backups)
python -c "from hub.secret_encryption import SecretCipher; print(SecretCipher.generate_key())"

# Variables de entorno (una de las dos, nunca ambas necesarias)
SECRET_ENCRYPTION_KEY=<clave-generada-arriba>
SECRET_ENCRYPTION_KEY_FILE=/ruta/a/un/archivo/con/la/clave   # alternativa, ej. secret montado por el orquestador

TOKEN=... # rol security-admin

# Guardar un secreto (el valor nunca se devuelve ni se audita después de este POST)
curl -s -X POST http://localhost:8000/admin/api/v1/secrets \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"name": "qradar-token", "value": "el-token-real-de-qradar"}'

curl -s http://localhost:8000/admin/api/v1/secrets -H "Authorization: Bearer $TOKEN"           # solo nombres
curl -s -X POST http://localhost:8000/admin/api/v1/secrets/qradar-token/test -H "Authorization: Bearer $TOKEN"  # descifra y confirma, sin exponer el valor
curl -s -X DELETE http://localhost:8000/admin/api/v1/secrets/qradar-token -H "Authorization: Bearer $TOKEN"
```

Cualquier `credential_ref` (destinos, alertas, el `client_secret` de OIDC) acepta hoy **dos** esquemas por igual, resueltos por el mismo punto único (`hub/credentials.py::resolve_credential_ref`):

- `env://NOMBRE` — variable de entorno del proceso.
- `secret://nombre` — secreto cifrado en reposo, guardado con el endpoint de arriba.

Rotación de la clave de cifrado: ver [docs/RUNBOOK.md](RUNBOOK.md) §3 (`POST /admin/api/v1/secrets/rotate-key`).

Esto es cifrado en reposo con clave externa, no una integración con un secret manager externo (HashiCorp Vault u otro) — ver [docs/ESTADO.md](ESTADO.md).

## 2) OIDC/SSO

```bash
OIDC_ISSUER_URL=https://idp.example.com/realms/hub          # IdP con descubrimiento OIDC estándar
OIDC_CLIENT_ID=hub-admin-ui
OIDC_CLIENT_SECRET_REF=secret://oidc-client-secret            # o env://OIDC_CLIENT_SECRET
OIDC_REDIRECT_URI=https://hub.example.com/admin/api/v1/auth/oidc/callback
OIDC_ROLE_CLAIM=roles                                          # nombre del claim del ID token con el/los rol(es)
OIDC_ROLE_MAPPING={"hub-security-admins": "security-admin", "hub-operators": "operator"}
OIDC_SESSION_TTL_SECONDS=28800
```

Con estas variables configuradas, la consola web muestra un botón "Entrar con SSO" en `/login` (navegación de página completa a `/admin/api/v1/auth/oidc/login`, no una ruta Angular — el redirect a un IdP externo y de vuelta necesita una recarga real del navegador). La sesión viaja en una cookie `hub_session` HttpOnly+Secure+SameSite=Lax (nunca visible a JS, a diferencia del token pegado a mano que vive en memoria — ver [docs/UI-ADMIN.md](UI-ADMIN.md) §1); `GET /admin/api/v1/auth/whoami` la detecta al arrancar la app. `POST /admin/api/v1/auth/logout` la revoca. Los tokens de API ([docs/API-ADMIN.md](API-ADMIN.md) §2) siguen funcionando sin cambios para automatización — OIDC es un mecanismo adicional, no un reemplazo.

**No validado contra un IdP real** (Keycloak/Okta/Azure AD) — los tests usan un IdP simulado con un keypair RSA propio. Antes de un despliegue real, probar el flujo completo contra el IdP corporativo elegido.

## 3) OpenTelemetry

```bash
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318   # sin esto, no se instala ningún exporter (aditivo/opcional)
OTEL_SERVICE_NAME=opencti-ioc-hub
```

Config de ejemplo del Collector en [deploy/otel-collector-config.yaml](../deploy/otel-collector-config.yaml) (incluye un processor que filtra atributos con pinta de secreto antes de exportar). Los spans instrumentados (`opencti.stream.receive`, `opencti.event.normalize`, `policy.evaluate`, `delivery.render`/`.send`/`.acknowledge`, `feed.rebuild`) ya están en el código — sin `OTEL_EXPORTER_OTLP_ENDPOINT` configurado, el Hub sigue funcionando igual.

## 4) Endurecimiento de producción

```bash
# Backup / restore -- ver docs/RUNBOOK.md para el procedimiento completo
python scripts/backup_state.py --state-dir "$HUB_STATE_DIR" --feed-dir "$TXT_FEED_DIR" --out-dir ./backups
python scripts/restore_state.py hub-backup-20260814T120000Z.tar.gz --state-dir ./state --feed-dir ./feeds

# Load test simple (sin dependencia nueva)
python scripts/load_test.py --base-url http://localhost:8000/admin/api/v1 \
  --token "$TOKEN" --endpoint /destinations --requests 200 --concurrency 20
```

Ver [docs/RUNBOOK.md](RUNBOOK.md) para el procedimiento completo (backup/restore con prueba mensual, recuperación ante caída de OpenCTI, rotación de la clave de secretos, recuperación de dead-letter). Caída de OpenCTI cubierta por un chaos test (`tests/hub/test_chaos_opencti_outage.py`) contra el backoff/reconexión ya construido.

## 5) Migración a PostgreSQL (solo documentada, sin construir)

Todo el estado durable hoy vive en SQLite (un archivo por módulo, cada uno con su propio `sqlite3.connect` + schema inline). Ruta recomendada para cuando se decida migrar a PostgreSQL (útil para producción multi-worker): introducir una fábrica de conexión por `DATABASE_URL` (eligiendo `sqlite3` o `psycopg` por esquema de la URL) en vez de un ORM completo — cambio mínimo sobre el patrón `init_db(path)` ya establecido; migrar primero los módulos de mayor escritura (`ledger`, `destinations_store`), después el resto. **Sin driver ni código de conexión Postgres en el repo todavía** — ver [docs/ESTADO.md](ESTADO.md).
