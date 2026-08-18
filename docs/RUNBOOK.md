# Runbook de operación y recuperación

**Autor:** Athan Espinoza

spec/09-ROADMAP-ACCEPTANCE.md Entrega 5 "Producción" ("chaos/recovery, backup/restore y rotación"); spec/06-OBSERVABILITY.md sección 10 ("Recuperación y runbooks": "Prueba mensual de backup/restore"). Este documento es el procedimiento operativo — para la referencia de comandos/endpoints ver `README.md` §13-§17.

## 1) Backup y restore

### Backup

```bash
python scripts/backup_state.py --state-dir "$HUB_STATE_DIR" --feed-dir "$TXT_FEED_DIR" --out-dir ./backups
```

Empaqueta **todas** las bases SQLite del Hub (destinos, políticas, ledger, tokens, sesiones OIDC, alertas, **secretos cifrados**, cursor, TAXII) más los feeds materializados, en un único `.tar.gz` con nombre `hub-backup-<timestamp>.tar.gz`. El archivo resultante contiene los secretos **cifrados** (nunca en claro) — la clave de cifrado (`SECRET_ENCRYPTION_KEY`/`_FILE`) vive fuera del backup, respaldarla por separado (ver sección 3).

### Restore

```bash
python scripts/restore_state.py hub-backup-20260814T120000Z.tar.gz --state-dir ./state --feed-dir ./feeds
# --force si el destino no esta vacio y se acepta sobrescribir
```

Rechaza sobrescribir un directorio no vacío sin `--force` (protección contra un restore accidental encima de un estado en uso), y valida que las entradas del archivo queden dentro de los prefijos esperados (protección contra path traversal si el archivo de origen no es confiable).

**Prueba mensual obligatoria** (spec/06): restaurar el último backup a un directorio temporal y verificar que `python -m hub.api` levanta contra ese estado sin errores, y que `GET /admin/api/v1/destinations`/`/policies` devuelven lo esperado. Documentar la fecha de la última prueba y quién la hizo.

## 2) Recuperación ante caída de OpenCTI

`hub.service` ya reconecta solo, con backoff exponencial (2s → 4s → 8s… hasta 60s, `hub/service.py::listen_live_stream`) y retoma desde el último cursor guardado (`Last-Event-ID`) — no se pierde el punto de partida por una caída de transporte. Verificado en `tests/hub/test_chaos_opencti_outage.py` (conexión simulada que falla y se recupera).

Si la caída se prolonga:
1. Confirmar con el operador de OpenCTI que el Live Stream está disponible.
2. `GET /admin/api/v1/ingestion/status` — revisar `heartbeat_age_seconds` (el proceso sigue vivo aunque no pueda conectar) y `cursor_updated_at`.
3. La alerta `opencti_disconnected` (spec/06, `hub/alert_rules.py`) se dispara sola pasado el umbral configurado (`ALERT_OPENCTI_DISCONNECTED_SECONDS`, default 120s) — no hace falta vigilar manualmente si el canal de alertas está configurado (§16.4 del README).
4. Una vez restablecida la conexión, `hub.service` reconecta en su próximo intento sin intervención manual.

## 3) Rotación de la clave de cifrado de secretos

```bash
NEW_KEY=$(python -c "from hub.secret_encryption import SecretCipher; print(SecretCipher.generate_key())")
curl -s -X POST http://localhost:8000/admin/api/v1/secrets/rotate-key \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d "{\"new_key\": \"$NEW_KEY\"}"
```

El proceso en ejecución ya usa la clave nueva después de esta llamada (sin reiniciar). **Paso obligatorio siguiente**: actualizar `SECRET_ENCRYPTION_KEY`/`SECRET_ENCRYPTION_KEY_FILE` en el entorno del Hub con `$NEW_KEY` antes del próximo reinicio del proceso — si el proceso reinicia con la clave vieja todavía configurada, los secretos guardados quedan indescifrables. Guardar `$NEW_KEY` en un lugar seguro (nunca en el propio repo/backups) antes de perder la clave vieja.

Rotación análoga para tokens de API: `POST /admin/api/v1/... ` no aplica — un token de API se rota creando uno nuevo (`hub/api/token_store.py::create_token`) y revocando el viejo (`revoke`), no hay un endpoint HTTP dedicado (mismo patrón documentado en README §14.2).

## 4) Recuperación de dead-letter

Ver README.md §14.3/§15.4 — `POST /admin/api/v1/deliveries/{id}/retry` (reintento manual) o `.../discard` (descartar con motivo obligatorio, no altera el evento original en el ledger). La alerta `dead_letter_nonzero` se dispara sola cuando hay entregas en dead-letter, con el conteo por destino.

## 5) Load test

```bash
python scripts/load_test.py --base-url http://localhost:8000/admin/api/v1 \
  --token "$TOKEN" --endpoint /destinations --requests 200 --concurrency 20
```

Script simple (sin dependencia nueva, `concurrent.futures` + `requests`) para una prueba rápida de regresión de latencia contra una instancia local/desarrollo — **no reemplaza** una herramienta de carga real (k6/locust/gatling) para un ambiente de staging serio; ver `spec/PROJECT-MAP.md` "Pendiente conocido: Entrega 5" para esta limitación documentada.

## 6) Límites conocidos de este runbook

Ver "Pendiente conocido: Entrega 5" en `spec/PROJECT-MAP.md`: sin chaos test de caída de destino/DB (solo caída de OpenCTI está cubierta), sin runbook de recuperación ante corrupción de una base SQLite puntual (el backup/restore completo es el único camino hoy), sin SLO de latencia por destino formalizado (Decisión #9 de spec/09 sigue abierta).

## 7) Crear y configurar el Live Stream de OpenCTI para este Hub

Diagnosticado en producción el 2026-08-18: sin un Live Stream bien configurado, el Hub puede quedar "conectado" en la UI (badge DEGRADED: "conectado, pero nunca recibió un evento real") sin entregar nada en tiempo real, aunque OpenCTI reciba IOC constantemente. Esta sección es la receta completa para que no vuelva a pasar.

### 7.1 Por qué hace falta

El backfill y la reconciliación (`hub/backfill.py`, `hub/reconcile.py`) solo hacen catch-up periódico por GraphQL — la reconciliación corre cada `RECONCILE_INTERVAL_SECONDS` (600s por defecto). El **Live Stream es la única vía de entrega en tiempo real** (create/update/delete al instante). Sin un stream que funcione, el Hub igual converge eventualmente, pero con hasta 10 minutos de atraso por defecto, no al instante.

### 7.2 Filtro correcto: solo `entity_type = Indicator`

`hub/normalize.py::classify_stix` únicamente sabe clasificar objetos STIX de tipo `indicator` (por `main_observable_type` en la extensión de OpenCTI). Cualquier otro tipo que el filtro del stream deje pasar explícitamente (`Malware`, `Threat-Actor`, etc.) se descarta siempre — no rompe nada (el Hub lo maneja de forma segura, ver `EVENT_SKIPPED` en 7.5), pero desperdicia ancho de banda del stream y ensucia los logs sin aportar nada. **No agregar otros tipos de entidad al filtro salvo que se extienda `hub/normalize.py` para soportarlos.**

**Aun con este filtro puesto, va a seguir viéndose `EVENT_SKIPPED` con regularidad — eso es esperado, no un filtro mal puesto.** Confirmado contra una instancia real: cuando un conector crea/actualiza un Indicator, OpenCTI manda por el Live Stream, en la misma tanda y con `origin.referer: "init-dependencies"`, los objetos de contexto asociados (`identity`, `marking-definition`, el observable crudo del que deriva el patrón — `ipv4-addr`, `url`, `text`, etc. — y la `relationship` que lo une al indicador), **sin importar el filtro `entity_type`**. Es el comportamiento estándar del Live Stream de OpenCTI (pensado para que un consumidor tenga el contexto completo sin lookups aparte), no algo que se pueda desactivar desde el filtro. En una muestra real de 40 eventos sobre un stream filtrado a solo `Indicator`, ~42% fueron `indicator` (create/update, lo único que el Hub usa) y ~58% fueron estos objetos de contexto — `EVENT_SKIPPED` en esa proporción es normal, no señal de que algo esté mal.

Filtro recomendado (formato `FilterGroup` de OpenCTI):

```json
{
  "mode": "and",
  "filters": [
    {"key": ["entity_type"], "operator": "eq", "values": ["Indicator"], "mode": "or"}
  ],
  "filterGroups": []
}
```

### 7.3 Opción A: crear el stream desde la UI de OpenCTI

1. **Data → Data sharing → Live streams → Create a stream**.
2. Nombre descriptivo (ej. `Feed Builder Hub - Indicators`) y descripción que deje claro que es específico de este Hub — no compartirlo con otro consumidor (QRadar, SOAR, etc.): un stream de otro equipo puede cambiar de filtro o borrarse sin aviso y tumbar la ingesta de este Hub sin que se note en el código.
3. Agregar el filtro `Entity type = Indicator` (equivalente al JSON de 7.2).
4. `Live` = sí. `Public` = no, salvo que se necesite explícitamente.
5. Guardar. El ID del stream es el UUID al final de la URL del stream (`.../stream/<uuid>`) — copiarlo tal cual, o pegar la URL completa: `hub/service.py::stream_url` soporta ambos formatos sin duplicar el path.

### 7.4 Opción B: crear el stream por API (scriptable)

Requiere un token con capacidad de administrar streams (ver 7.6 sobre qué token usar).

```bash
curl -sk -X POST "$OPENCTI_URL/graphql" \
  -H "Authorization: Bearer $OPENCTI_TOKEN" -H "Content-Type: application/json" \
  -d '{
    "query": "mutation CreateStream($input: StreamCollectionAddInput!) { streamCollectionAdd(input: $input) { id name filters stream_live stream_public } }",
    "variables": {
      "input": {
        "name": "Feed Builder Hub - Indicators",
        "description": "Live Stream dedicado para hub/ (feed-builder): solo Indicator.",
        "filters": "{\"mode\":\"and\",\"filters\":[{\"key\":[\"entity_type\"],\"operator\":\"eq\",\"values\":[\"Indicator\"],\"mode\":\"or\"}],\"filterGroups\":[]}",
        "stream_live": true,
        "stream_public": false
      }
    }
  }'
```

La respuesta trae el `id` del stream recién creado.

### 7.5 Apuntar el Hub al stream y verificar

```bash
curl -sk -X PUT https://$PUBLIC_HOST:$NGINX_HTTPS_PORT/admin/api/v1/opencti-settings \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"url": "https://opencti.example.local:8443", "tls_verify": false, "stream_id": "<uuid-del-stream>"}'
```

`token` se puede omitir en este PUT si ya hay uno guardado (se conserva, ver `hub/api/schemas.py::OpenCTISettingsUpdate`) — no hace falta repegarlo solo para cambiar el `stream_id`. `url` sí es obligatorio en cada PUT aunque no cambie.

Verificación:

1. `POST /admin/api/v1/opencti-settings/test` → `{"ok": true}`.
2. `docker compose logs -f hub-service` → debe verse `Connecting SSE: .../stream/<uuid-nuevo>` sin reconexiones constantes por 401/timeout.
3. En régimen normal, **sin ningún `EVENT_PROCESS_ERROR`** (eso sí sería un problema real). `EVENT_SKIPPED` en cambio es esperado y puede ser más de la mitad del tráfico del stream (ver 7.2: OpenCTI manda los objetos de contexto de cada Indicator aunque el filtro solo pida `Indicator`) — no es señal de que el filtro esté mal puesto.
4. Confirmar entrega real end-to-end: contar filas de `event_ledger` (`sqlite3 $HUB_STATE_DIR/ledger.sqlite3 "SELECT COUNT(*) FROM event_ledger"`) antes y después de esperar 1-2 minutos — debería crecer sin que haya corrido un backfill/reconciliación en el medio.

### 7.6 Gotcha de plataforma ya resuelto en el código (para no re-diagnosticarlo)

El payload `data:` del Live Stream de OpenCTI **no trae un campo `"action"`** — confirmado contra una instancia real, el shape es `{"data": {...STIX...}, "message": ..., "origin": ..., "version": "4"}`. La acción real (`create`/`update`/`delete`) viene **solo** en la línea SSE `event:`, que antes del 2026-08-18 `hub/sse.py` descartaba por completo. Esto hacía que el Live Stream se conectara "bien" pero nunca entregara un evento real — todo caía en `EVENT_PROCESS_ERROR: accion de envelope desconocida: None` (o `'str' object has no attribute 'get'` para los heartbeats, cuyo payload es un string JSON, no un dict).

Ya está resuelto: `hub/sse.py::SSEEvent.event` conserva la línea `event:`, y `hub/service.py::listen_live_stream` la inyecta como `envelope["action"]` antes de procesar, ignorando en silencio los control frames (`heartbeat`, `connected`, `consumer_metrics`) que no traen ningún IOC. Si en algún momento se ve `EVENT_PROCESS_ERROR: accion de envelope desconocida: None` otra vez, ese fix se perdió o se revirtió — **no es un problema de configuración de OpenCTI ni del filtro del stream**, hay que revisar `hub/sse.py`/`hub/service.py` directamente.

### 7.7 Cuenta de servicio: usar una dedicada, no el usuario admin

Sección 4.2 del README ya pide "token de cuenta de servicio (no administrativa) con permisos de stream y GraphQL". En la práctica, crear un stream (mutación `streamCollectionAdd`) requiere permisos de administración de streams en OpenCTI — conviene crear el stream una vez con una cuenta con esos permisos, y después generar/asignar al Hub un token de una cuenta de servicio de solo lectura (GraphQL + consumo del stream específico), no el token personal del administrador. Si el Hub queda corriendo con el token de un admin real (capacidad `BYPASS`), cualquier fuga de ese token (logs, backup sin cifrar, etc.) compromete la instancia completa de OpenCTI, no solo la ingesta de IOC.

### 7.8 Relacionado: filtro de tipos en backfill/reconciliación (no es este stream, pero es la misma familia de problema)

Aparte del Live Stream, `hub/backfill.py`/`hub/reconcile.py` traen indicadores por GraphQL (no por el stream) para el catch-up inicial y periódico. Esa consulta también filtra por `x_opencti_main_observable_type` (`hub/graphql_indicator.py::BACKFILL_SUPPORTED_OBSERVABLE_TYPES`) por el mismo motivo que 7.2: sin ese filtro, tipos sin adaptador (`Artifact`, `Text`, etc.) pueden consumir casi todo el cupo de páginas (`BACKFILL_MAX_PAGES`/`BACKFILL_PAGE_SIZE`) antes de llegar a los IOC reales. Si se agrega soporte para un tipo de observable nuevo en `hub/normalize.py`, agregarlo también a esa lista — si no, el backfill lo sigue descartando aunque el Live Stream ya lo entregue bien.
