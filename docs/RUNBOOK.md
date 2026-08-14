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
