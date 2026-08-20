# Feed Builder — Hub de Distribución de IOC para OpenCTI

```text
⚠️ Proyecto en beta. Úsalo con cuidado: el núcleo está construido y probado,
pero todavía no se validó contra una instancia de OpenCTI real en producción.
Ver "Estado del proyecto" más abajo antes de usarlo en serio.
```

## ¿Qué hace esto, en criollo?

Tu equipo de seguridad carga indicadores de amenaza (IPs maliciosas, dominios, hashes de malware, etc.) en **OpenCTI**. El problema de siempre: cada firewall, antivirus o SIEM que necesita bloquear esos indicadores quiere la lista en un formato distinto, y alguien termina exportándola y pegándola a mano, una y otra vez.

Este **Hub** resuelve justamente eso. Se conecta a OpenCTI y escucha en tiempo real cada indicador nuevo o modificado, le aplica las reglas que vos definas para cada destino (qué tipo de indicador, con qué nivel de confianza, cuánto tiempo vive), y publica el resultado ya filtrado en el formato que cada destino entiende: lista de texto plano, CSV, RouterOS, CDB de Wazuh, JSON, STIX/TAXII, etc. Todo eso se administra desde una consola web, sin tocar archivos de configuración a mano.

En una frase: **OpenCTI decide qué es una amenaza; este Hub decide quién se entera y en qué formato.**

### ¿Qué NO es esto?

- No reemplaza OpenCTI ni lo instala: corre *sobre* una instancia de OpenCTI que ya está funcionando, por separado.
- No es un firewall ni un antivirus: no bloquea nada por sí mismo, solo le entrega las listas ya filtradas a las herramientas que sí bloquean.

## Estado del proyecto

Versión actual: `0.1.8` — historial completo en [CHANGELOG.md](CHANGELOG.md).

El núcleo (ingesta desde OpenCTI, motor de políticas, entrega a destinos, Admin API, consola web) está construido y con pruebas automatizadas, pero corridas contra datos y OpenCTI simulados — **todavía no se probó contra una instancia OpenCTI real en producción**. Antes de confiar en esto para un caso real, revisar [docs/ESTADO.md](docs/ESTADO.md), que detalla qué está construido, qué está probado y qué falta, componente por componente.

## Inicio rápido

Requiere Docker + Docker Compose. El Hub arranca **standalone**: no necesita saber nada de OpenCTI para levantarse — esa conexión se configura después, en caliente, desde la consola web.

```bash
cp .env.example .env
# completar PUBLIC_HOST en .env (y NGINX_HTTPS_PORT si el default 8446 está ocupado)
docker compose up -d
docker compose ps   # los 3 contenedores deben quedar Up/healthy
```

Con el Hub arriba, el siguiente paso es generar el primer token de acceso y conectarlo a tu OpenCTI desde la consola web. Los pasos completos, con ejemplos, están en [docs/INSTALACION.md](docs/INSTALACION.md).

## Qué hay en este repo

| Carpeta / archivo | Qué es |
| --- | --- |
| `hub/` | El Hub en sí: ingesta desde OpenCTI, motor de políticas, adaptadores de entrega, Admin API. |
| `ui/` | Consola web (Angular) para administrar destinos y políticas, y ver el estado del sistema. |
| `docker-compose.yml`, `nginx.conf`, `.env.example` | Cómo se despliega el Hub. No incluyen OpenCTI, que corre aparte. |
| `opencti_feed_builder.py` | Script antiguo, ya reemplazado por `hub/`. Se mantiene solo como referencia histórica — ver [docs/SCRIPT-LEGADO.md](docs/SCRIPT-LEGADO.md). |
| `docs/` | Toda la documentación detallada, enlazada abajo. |
| `tests/` | Suite de pruebas automatizadas (`pytest`). |

## Documentación

- [docs/INSTALACION.md](docs/INSTALACION.md) — requisitos, variables de entorno y puesta en marcha paso a paso.
- [docs/API-ADMIN.md](docs/API-ADMIN.md) — Admin API: autenticación y ejemplos (destinos, políticas, auditoría, control de ingesta).
- [docs/UI-ADMIN.md](docs/UI-ADMIN.md) — consola web: cómo correrla, qué secciones tiene, cómo correr sus tests.
- [docs/INTEGRACIONES.md](docs/INTEGRACIONES.md) — catálogo de destinos soportados (Fortinet, Palo Alto, Check Point, MikroTik, Wazuh, QRadar, TAXII, alertas por email/webhook) y cómo dar de alta cada uno.
- [docs/PRODUCCION.md](docs/PRODUCCION.md) — secretos cifrados, login SSO/OIDC, trazas (OpenTelemetry) y endurecimiento para producción.
- [docs/OPERACION.md](docs/OPERACION.md) — comandos habituales, problemas comunes y buenas prácticas de seguridad.
- [docs/RUNBOOK.md](docs/RUNBOOK.md) — procedimientos de recuperación: backup/restore, caída de OpenCTI, rotación de claves.
- [docs/ESTADO.md](docs/ESTADO.md) — qué está construido, qué está probado y qué falta, componente por componente.
- [docs/SCRIPT-LEGADO.md](docs/SCRIPT-LEGADO.md) — documentación del script `opencti_feed_builder.py`, ya reemplazado por `hub/`.
- [CHANGELOG.md](CHANGELOG.md) — historial de versiones.

## Seguridad

- Nunca versionar el `.env` con secretos reales (ver `.gitignore`).
- Restringir el acceso al puerto HTTPS publicado solo a los equipos autorizados.
- Más prácticas recomendadas en [docs/OPERACION.md](docs/OPERACION.md).

---

🇬🇧 English version: [README.en.md](README.en.md)
