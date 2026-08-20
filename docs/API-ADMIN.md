# Admin API

FastAPI en `hub/api/`. Comparte el mismo `HUB_STATE_DIR` que `hub.service` pero es un proceso separado (así se puede reiniciar/escalar la API sin tocar la ingesta, y viceversa).

Ver también: [docs/ESTADO.md](ESTADO.md) para qué endpoints están probados contra sistemas reales y cuáles no.

## 1) Correr el Admin API

```bash
python -m hub.api
# variables opcionales: HUB_API_HOST (default 0.0.0.0), HUB_API_PORT (default 8000)
```

Docs interactivas en `/admin/api/v1/docs`, OpenAPI en `/admin/api/v1/openapi.json`.

## 2) Autenticación

Hoy se soportan dos mecanismos, cualquiera de los dos alcanza para autenticarse (`hub/api/auth.py::require_role` acepta ambos):

- **Tokens de API locales** (el mecanismo pensado para automatización/scripts): un token se genera una sola vez, se persiste solo su hash. No hay endpoint HTTP para crear el primer token (sería un huevo-y-gallina de auth); se genera con un script corto contra el mismo `tokens.sqlite3`:

  ```bash
  python -c "
  from hub.api.token_store import init_db, create_token
  conn = init_db('/ruta/a/HUB_STATE_DIR/tokens.sqlite3')
  token, plaintext = create_token(conn, role='security-admin')
  print(plaintext)  # guardarlo ahora: no se puede volver a mostrar
  "
  ```

- **Sesión OIDC/SSO** (pensado para personas usando la consola web con el proveedor de identidad de la organización): ver [docs/PRODUCCION.md](PRODUCCION.md) §2 para la configuración completa.

Roles (jerárquicos: uno mayor cubre las acciones de uno menor): `viewer` < `operator` < `policy-admin` < `security-admin`.

## 3) Ejemplo: configurar OpenCTI + alta de un destino TXT + política + publicación

```bash
TOKEN=... # token con rol security-admin/policy-admin según el paso

# Configurar la conexión a OpenCTI (una vez; se puede repetir para rotar
# el token o cambiar la URL sin reiniciar el proceso, ver hub/opencti_settings_store.py).
curl -s -X PUT http://localhost:8000/admin/api/v1/opencti-settings \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"url": "https://opencti.example.local:8443", "token": "<token-de-cuenta-de-servicio>"}'

# Probar la conexión antes de esperar a que hub-service la levante sola.
curl -s -X POST http://localhost:8000/admin/api/v1/opencti-settings/test -H "Authorization: Bearer $TOKEN"

curl -s -X POST http://localhost:8000/admin/api/v1/destinations \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"destination_id": "fortigate-prod", "name": "Fortigate", "adapter": "txt_feed",
       "capacity": {"max_records_per_file": 20000}}'

curl -s -X POST http://localhost:8000/admin/api/v1/policies \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"policy_id": "fortigate-policy", "destination_id": "fortigate-prod",
       "allowed_iocs": [{"family": "hash", "subtypes": ["sha256"]}], "ttl_days": {"sha256": 60}}'

curl -s -X POST http://localhost:8000/admin/api/v1/policies/fortigate-policy/publish \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"version": 1}'
```

Un destino sin política publicada no recibe entregas.

## 4) Auditoría, búsqueda de eventos y control de ingestión

```bash
# Auditoría (rol viewer), filtrable por actor/acción/recurso/fecha
curl -s "http://localhost:8000/admin/api/v1/audit?resource_type=destination" -H "Authorization: Bearer $TOKEN"

# Buscar entregas en el ledger (rol viewer)
curl -s "http://localhost:8000/admin/api/v1/events?stix_id=indicator--..." -H "Authorization: Bearer $TOKEN"
curl -s "http://localhost:8000/admin/api/v1/events/{event_id}" -H "Authorization: Bearer $TOKEN"  # línea de tiempo

# Control de ingestión: pausar/reanudar y reconciliar (rol operator)
curl -s -X POST http://localhost:8000/admin/api/v1/ingestion/pause -H "Authorization: Bearer $TOKEN"
curl -s -X POST http://localhost:8000/admin/api/v1/ingestion/reconcile -H "Authorization: Bearer $TOKEN"

# Rebobinar cursor (rol security-admin, motivo obligatorio)
curl -s -X POST http://localhost:8000/admin/api/v1/ingestion/rewind \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"cursor_value": "<id-de-evento-sse-anterior>", "reason": "brecha detectada en reconciliación"}'
```

`hub.service` (ingestión) y `hub.api` (Admin API) son procesos separados: los pedidos de pausar/reconciliar/rebobinar se escriben en `ingestion_control.sqlite3` (`hub/ingestion_control.py`) y el loop de `hub.service` los aplica sondeando esa tabla, no hay llamada directa entre procesos.

## 5) Tests

```bash
pip install -r requirements-dev.txt
pytest
```

La suite de `tests/hub/` cubre normalización, dedup, TTL, cursor, ledger, SSE, cliente GraphQL, backfill, reconciliación, escritura de feeds, y la Admin API completa (destinos, políticas, adapters, retries/circuit breaker, endpoints FastAPI) contra fixtures, GraphQL simulado y `requests` simulado — no contra una instancia OpenCTI o un destino real (ver [docs/ESTADO.md](ESTADO.md)).

## 6) Backup y restore

El estado durable vive en `HUB_STATE_DIR` (`cursor.sqlite3`, `ledger.sqlite3`, `destinations.sqlite3`, `policies.sqlite3`, `tokens.sqlite3`, `idempotency.sqlite3`, `audit.sqlite3`, `ingestion_control.sqlite3`, `opencti_settings.sqlite3`, `secrets.sqlite3`, `.heartbeat`) y los feeds materializados en `TXT_FEED_DIR`. Procedimiento completo en [docs/RUNBOOK.md](RUNBOOK.md) §1.
