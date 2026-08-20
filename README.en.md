# Feed Builder — IOC Distribution Hub for OpenCTI

```text
⚠️ Beta project. Use with care: the core is built and tested, but it has
not yet been validated against a real production OpenCTI instance.
See "Project status" below before relying on this for anything serious.
```

*(Detailed docs linked below are in Spanish — see [README.md](README.md) for the Spanish version of this page.)*

## What does this actually do?

Your security team loads threat indicators (malicious IPs, domains, malware hashes, etc.) into **OpenCTI**. The usual problem: every firewall, antivirus, or SIEM that needs to block those indicators wants the list in a different format, and someone ends up exporting it and pasting it in by hand, over and over.

This **Hub** solves exactly that. It connects to OpenCTI and listens in real time for every new or updated indicator, applies the rules you define per destination (which indicator types, what confidence level, how long they stay valid), and publishes the filtered result in whatever format each destination understands: plain text list, CSV, RouterOS, Wazuh CDB, JSON, STIX/TAXII, etc. All of it is managed from a web console, with no manual config-file editing.

In one sentence: **OpenCTI decides what counts as a threat; this Hub decides who finds out, and in what format.**

### What this is NOT

- It doesn't replace or install OpenCTI: it runs *on top of* an OpenCTI instance that's already up and running, separately.
- It's not a firewall or an antivirus: it doesn't block anything itself — it just hands the already-filtered lists to the tools that do.

## Project status

Current version: `0.1.8` — full history in [CHANGELOG.md](CHANGELOG.md).

The core (OpenCTI ingestion, policy engine, delivery to destinations, Admin API, web console) is built and covered by automated tests, but those tests run against simulated data and a simulated OpenCTI — **it has not yet been tested against a real production OpenCTI instance**. Before trusting this for a real deployment, check [docs/ESTADO.md](docs/ESTADO.md) (in Spanish), which details what's built, what's tested, and what's missing, component by component.

## Quick start

Requires Docker + Docker Compose. The Hub starts up **standalone**: it doesn't need to know anything about OpenCTI to come up — that connection is configured afterward, live, from the web console.

```bash
cp .env.example .env
# fill in PUBLIC_HOST in .env (and NGINX_HTTPS_PORT if the default 8446 is taken)
docker compose up -d
docker compose ps   # all 3 containers should be Up/healthy
```

Once the Hub is up, the next step is generating your first access token and connecting it to your OpenCTI instance from the web console. Full steps with examples are in [docs/INSTALACION.md](docs/INSTALACION.md) (in Spanish).

## What's in this repo

| Folder / file | What it is |
| --- | --- |
| `hub/` | The Hub itself: OpenCTI ingestion, policy engine, delivery adapters, Admin API. |
| `ui/` | Web console (Angular) for managing destinations and policies, and viewing system status. |
| `docker-compose.yml`, `nginx.conf`, `.env.example` | How the Hub gets deployed. Does not include OpenCTI, which runs separately. |
| `opencti_feed_builder.py` | Old script, already replaced by `hub/`. Kept only as historical reference — see [docs/SCRIPT-LEGADO.md](docs/SCRIPT-LEGADO.md). |
| `docs/` | All the detailed documentation, linked below. |
| `tests/` | Automated test suite (`pytest`). |

## Documentation

All of the following are in Spanish:

- [docs/INSTALACION.md](docs/INSTALACION.md) — requirements, environment variables, and step-by-step setup.
- [docs/API-ADMIN.md](docs/API-ADMIN.md) — Admin API: authentication and examples (destinations, policies, audit, ingestion control).
- [docs/UI-ADMIN.md](docs/UI-ADMIN.md) — web console: how to run it, what sections it has, how to run its tests.
- [docs/INTEGRACIONES.md](docs/INTEGRACIONES.md) — catalog of supported destinations (Fortinet, Palo Alto, Check Point, MikroTik, Wazuh, QRadar, TAXII, email/webhook alerts) and how to set up each one.
- [docs/PRODUCCION.md](docs/PRODUCCION.md) — encrypted secrets, SSO/OIDC login, tracing (OpenTelemetry), and production hardening.
- [docs/OPERACION.md](docs/OPERACION.md) — common commands, common problems, and security best practices.
- [docs/RUNBOOK.md](docs/RUNBOOK.md) — recovery procedures: backup/restore, OpenCTI outage, key rotation.
- [docs/ESTADO.md](docs/ESTADO.md) — what's built, what's tested, and what's missing, component by component.
- [docs/SCRIPT-LEGADO.md](docs/SCRIPT-LEGADO.md) — documentation for the old `opencti_feed_builder.py` script, already replaced by `hub/`.
- [CHANGELOG.md](CHANGELOG.md) — version history.

## Security

- Never commit `.env` with real secrets (see `.gitignore`).
- Restrict access to the published HTTPS port to authorized teams only.
- More recommended practices in [docs/OPERACION.md](docs/OPERACION.md) (in Spanish).

---

🇪🇸 Versión en español: [README.md](README.md)
