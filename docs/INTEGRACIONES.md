# Integraciones y destinos soportados

Catálogo de destinos, agrupados por el esfuerzo real que costó soportarlos.

## 1) Bajo esfuerzo (reusan el adapter `txt_feed` existente, sin código nuevo)

Fortinet/FortiGate (External Connector), Palo Alto PAN-OS (External Dynamic List), pfSense/pfBlockerNG, Cisco Security Intelligence: los cuatro consumen un feed TXT plano (un IOC por línea, feeds separados por subtipo) — exactamente lo que `adapter: "txt_feed"` ya escribe. Alta vía `POST /admin/api/v1/destinations` con `"adapter": "txt_feed"`, sin campos adicionales.

Recordá la escalera de autenticación cuando el fabricante hace *poll* sobre la URL del Hub — nunca publicar sin al menos un control de acceso: Basic Auth cuando el fabricante lo soporta (Fortinet, Palo Alto), token no adivinable en la URL cuando no (Cisco Security Intelligence, pfBlockerNG), o mTLS si ya se gestiona PKI interna (Palo Alto).

## 2) Esfuerzo medio (adapters nuevos, formato propio o API dedicada)

```bash
TOKEN=... # rol security-admin

# Check Point (CSV multi-columna, columnas configurables)
curl -s -X POST http://localhost:8000/admin/api/v1/destinations \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"destination_id": "checkpoint-prod", "name": "Check Point", "adapter": "csv_feed", "format": "csv",
       "format_options": {"columns": ["family", "subtype", "value", "score", "confidence", "marking", "created_at", "valid_until"]}}'

# MikroTik RouterOS (.rsc, solo IP/CIDR -- address-list no acepta dominios ni hashes)
curl -s -X POST http://localhost:8000/admin/api/v1/destinations \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"destination_id": "mikrotik-prod", "name": "MikroTik", "adapter": "mikrotik_rsc", "format": "rsc",
       "format_options": {"list_name": "hub-blocklist"}}'

# Wazuh (CDB list -- el Hub solo materializa el archivo; sincronizarlo al manager y recargar es responsabilidad externa)
curl -s -X POST http://localhost:8000/admin/api/v1/destinations \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"destination_id": "wazuh-prod", "name": "Wazuh", "adapter": "wazuh_cdb", "format": "cdb"}'

# QRadar (Reference Set API, bulk_load)
curl -s -X POST http://localhost:8000/admin/api/v1/destinations \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"destination_id": "qradar-refset", "name": "QRadar", "adapter": "qradar_reference_set", "format": "json",
       "endpoint": "https://qradar.example", "credential_ref": "env://QRADAR_SEC_TOKEN",
       "format_options": {"reference_set_name": "hub-malicious-ips"}}'
```

MikroTik: el Hub solo genera el bloque `add address=... list=... timeout=...`; envolverlo en el propio Scheduler/`/tool fetch` del router queda del lado del operador (no hay una plantilla `.rsc` completa documentada). Wazuh: el archivo CDB generado en `TXT_FEED_DIR/<destino>/<subtipo>.cdb` debe sincronizarse al filesystem del manager y disparar el reload por fuera del Hub — se resolvió así a propósito para no construir un agente/sync-companion con su propia superficie de credenciales SSH.

## 3) Alto esfuerzo: STIX 2.1 y servidor TAXII 2.1

`stix_bundle_feed` materializa un `bundle.json` por destino (Bundle STIX 2.1 con todos los indicators vigentes). `taxii2` expone esos mismos indicators vía un servidor TAXII 2.1 mínimo y de solo lectura que corre el propio Hub — se construyó uno propio (en vez de depender solo del TAXII nativo de OpenCTI) porque es el Hub quien filtra por política antes de exponer el IOC a un tercero:

```bash
curl -s -X POST http://localhost:8000/admin/api/v1/destinations \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"destination_id": "cisco-tid", "name": "Cisco TID", "adapter": "taxii2", "format": "stix2.1",
       "credential_ref": "env://TAXII_CISCO_BASIC_AUTH"}'
```

`env://TAXII_CISCO_BASIC_AUTH` debe contener `usuario:password` (mismo secreto resuelto por `hub/credentials.py`, formato Basic Auth). Consumo (Cisco TID u otro cliente TAXII 2.1):

```bash
curl -s http://localhost:8000/taxii2/                                          # discovery, público
curl -s http://localhost:8000/taxii2/hub/collections/                          # lista, público (solo metadata)
curl -s -u cisco:s3cret http://localhost:8000/taxii2/hub/collections/cisco-tid/objects/
```

El Hub es productor: `POST` a `.../objects/` devuelve 405. Una colección TAXII es append/update-only — descartar/revocar un IOC republica el mismo objeto con `revoked: true`, nunca lo borra (a diferencia de `stix_bundle_feed`, que sí remueve el objeto del bundle porque es una foto del estado actual, no un log).

## 4) Alertas email/webhook

```bash
# Variables de entorno relevantes (todas opcionales; sin ninguna, no se notifica por ningún canal)
ALERT_COOLDOWN_SECONDS=300                  # minimo entre notificaciones repetidas de la misma alerta
ALERT_SMTP_HOST=smtp.example.internal
ALERT_SMTP_PORT=587
ALERT_EMAIL_FROM=hub@example.internal
ALERT_EMAIL_TO=soc@example.internal,ops@example.internal
ALERT_EMAIL_CREDENTIAL_REF=env://ALERT_SMTP_CREDENTIALS   # "usuario:password"
ALERT_EMAIL_MIN_SEVERITY=warning            # info | warning | critical
ALERT_WEBHOOK_URL=https://ops.example/hooks/hub-alerts
ALERT_WEBHOOK_CREDENTIAL_REF=env://ALERT_WEBHOOK_SECRET   # secreto HMAC-SHA256
ALERT_WEBHOOK_MIN_SEVERITY=info

# Disparar evaluación manualmente (o vía cron externo -- sin scheduler real todavía)
curl -s -X POST http://localhost:8000/admin/api/v1/alerts/evaluate -H "Authorization: Bearer $TOKEN"
curl -s http://localhost:8000/admin/api/v1/alerts -H "Authorization: Bearer $TOKEN"
curl -s -X POST http://localhost:8000/admin/api/v1/alerts/{alert_id}/acknowledge -H "Authorization: Bearer $TOKEN"
```

`hub.service` (el proceso de ingestión) además evalúa automáticamente, una vez por minuto en su propio loop, las 2 condiciones que dependen de su estado in-process (OpenCTI desconectado, cursor sin avanzar); el resto (dead-letter, destino sin entrega, feed sin rebuild) se evalúan desde el Admin API porque es ese proceso el que tiene ledger/destinos/feeds a mano.

---

Ver [docs/ESTADO.md](ESTADO.md) para qué adapters están validados contra un sistema real y cuáles no.
