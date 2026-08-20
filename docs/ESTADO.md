# Estado del proyecto: qué está construido, qué está probado y qué falta

Versión de referencia: `0.1.8` — ver [CHANGELOG.md](../CHANGELOG.md) para el historial completo por versión. Este documento se enfoca en algo distinto: no es un changelog, es una foto honesta de qué tan lista está cada parte del Hub hoy.

## Resumen

El Hub (`hub/`) está funcionalmente completo para el caso de uso principal: ingesta desde OpenCTI, motor de políticas por destino, entrega a más de una decena de tipos de destino, Admin API completa, y una consola web operativa. Todo eso tiene pruebas automatizadas (`pytest`, `pytest tests/hub/`, Vitest y Playwright para la UI).

Lo que **no** está probado todavía es contra sistemas reales de terceros: todas las pruebas corren contra fixtures, un OpenCTI simulado y un `requests`/IdP simulados — no contra una instancia OpenCTI real, ni contra un firewall/SIEM/IdP de producción. Ese es el riesgo principal a tener en cuenta antes de un despliegue serio.

## Por componente

| Componente | Qué hace | Probado contra sistema real |
| --- | --- | --- |
| Ingesta OpenCTI (Live Stream + backfill + reconciliación) | Se conecta al stream SSE, normaliza STIX, reconcilia por GraphQL cada `RECONCILE_INTERVAL_SECONDS`, cursor durable con recuperación tras reinicio | No — nombres de campo GraphQL (`x_opencti_main_observable_type`, `observables`, etc.) siguen la documentación pública de OpenCTI, no un esquema introspectado contra una instancia real |
| Motor de políticas | Evalúa cada evento contra `family/subtype`, TTL y `revoked` de la política publicada del destino; versionado inmutable (draft/published/superseded/rolled_back); simulación de política candidata antes de publicar | Sí, lógica probada exhaustivamente con fixtures. Falta: filtros por score/confidence mínimo, TLP/markings, labels, ventana horaria, tamaño de batch |
| Entrega / adapters | Contrato común `validate/render/send/acknowledge/healthcheck/close`; reintentos con backoff/jitter y circuit breaker por destino; dead-letter en el ledger | Adapters de archivo (`txt_feed`, `csv_feed`, `.rsc`, CDB, bundle STIX) sí, porque solo escriben a disco. El adapter HTTP push (tipo QRadar) y TAXII **no** están probados contra un endpoint/consumidor real |
| Admin API | CRUD de destinos/políticas, auditoría, búsqueda de eventos, control de ingesta, alertas, secretos, auth por token u OIDC | Sí, con la suite de `tests/hub/` |
| Consola web (Angular) | Las 7 secciones operativas + "Conexión OpenCTI"; RBAC del lado del cliente | Sí, unitarios (Vitest) y E2E (Playwright, 7/7 en verde) |
| Secretos cifrados en reposo | `credential_ref` acepta `env://NAME` y `secret://nombre` por igual, mismo punto de resolución | Cifrado/descifrado sí probado. No es una integración con un secret manager externo real (Vault, AWS Secrets Manager) |
| OIDC/SSO | Authorization Code + PKCE, cookie de sesión HttpOnly | Probado contra un IdP simulado (keypair RSA propio en los tests). **No validado contra un IdP real** (Keycloak/Okta/Azure AD) |
| Trazas OpenTelemetry | 7 spans instrumentados, aditivo/opcional | Sí — sin endpoint configurado, el Hub sigue funcionando igual (no-op) |
| Backup/restore, chaos test, load test | Scripts en `scripts/`, procedimiento en [docs/RUNBOOK.md](RUNBOOK.md) | Backup/restore sí probado. Chaos test cubre solo caída de OpenCTI (no caída de un destino o de una base SQLite puntual). Load test es un script simple, no una herramienta real tipo k6/locust |
| PostgreSQL | — | **No construido**, solo documentada la ruta de migración ([docs/PRODUCCION.md](PRODUCCION.md) §5). Todo el estado sigue en SQLite hoy |

## Lo que falta explícitamente (sin fingir que existe)

- **Sin cola/workers en segundo plano**: cada entrega es un intento por llamada; el reintento siguiente lo dispara `POST /deliveries/{id}/retry` (manual o cron externo) o el próximo evento del mismo destino.
- **Sin Canonical Event Store**: el ledger guarda identidad y estado de entrega de cada evento, no el `family`/`subtype`/valor normalizado — eso acota qué puede mostrar el Inspector del Event Ledger en la UI.
- **Sin log store real / visor de logs en vivo**: `hub.service`/`hub.api` solo imprimen a stdout. La UI lo marca explícitamente como "no disponible" en vez de simularlo.
- **Sin SSE/WebSocket real en el Admin API**: la consola web usa polling corto.
- **Sin configuración operativa editable desde la UI** (umbrales, retención): no existen los endpoints todavía.
- **Sin preview de volumen al rebobinar el cursor**: no hay forma barata de contar eventos entre dos cursores sin re-consultar OpenCTI extensamente.
- **Sin archivo histórico de IOC** (más allá del feed vigente).
- **5 de las 11 condiciones de alerta pensadas** (espacio en disco, TLS inválido, caída de volumen histórico, entre otras) sin señal disponible todavía — no se inventaron valores falsos, esas alertas simplemente no existen hoy.
- **Sin SLO de latencia configurable por destino**: la alerta de "destino sin entrega reciente" usa un único umbral a nivel Hub.
- **Rate limiting solo en memoria del proceso**: no sobrevive un reinicio ni escala a más de un worker.

## Cómo se mantiene actualizado este documento

Cuando un cambio agrega, quita o valida contra un sistema real algo de la tabla de arriba, este archivo se actualiza en el mismo cambio.
