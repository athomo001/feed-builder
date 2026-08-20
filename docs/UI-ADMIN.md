# Consola web (UI Angular)

Consola operativa en `ui/`, consume el [Admin API](API-ADMIN.md). Angular **21.x** (no 22.x: Angular CLI 22.x exige Node ≥24.15.0/22.22.3/26 y el entorno de build tenía Node 24.13.0 sin gestor de versión disponible para subir; sustitución documentada en [CHANGELOG.md](../CHANGELOG.md) `[0.1.5]`).

## 1) Instalar y correr en desarrollo

```bash
cd ui
npm install
npx ng serve   # http://localhost:4200, apunta a http://localhost:8000 (ver src/environments/environment.development.ts)
```

El Admin API debe correr por separado ([docs/API-ADMIN.md](API-ADMIN.md) §1) **con** `ADMIN_UI_ORIGINS=http://localhost:4200` seteado (CORS cerrado por defecto, se habilita explícitamente al origen real de la UI):

```bash
ADMIN_UI_ORIGINS=http://localhost:4200 python -m hub.api
```

Login: pegar un API token ya generado ([docs/API-ADMIN.md](API-ADMIN.md) §2) y elegir el rol con el que se creó, o entrar con SSO si hay OIDC configurado ([docs/PRODUCCION.md](PRODUCCION.md) §2). El token pegado vive solo en memoria de la pestaña (sin `localStorage`/`sessionStorage`): se pierde al refrescar la página, por diseño.

## 2) Build de producción

```bash
cd ui
npx ng build
```

Genera `ui/dist/ui/`, para servir detrás del mismo Nginx/dominio que expone el Admin API (mismo origen evita configurar `ADMIN_UI_ORIGINS`; `src/environments/environment.ts` asume `apiBaseUrl: '/admin/api/v1'` relativo).

## 3) Tests unitarios y E2E

```bash
cd ui
npx ng test --watch=false       # Vitest, unitarios

npx playwright install chromium # una sola vez
npx playwright test             # E2E: levanta backend Python + ng serve automáticamente
```

El E2E (`ui/e2e/`) siembra un token `security-admin` y una entrega en dead-letter en un `state_dir` temporal que se limpia al inicio de cada corrida (para que sea reproducible), y no depende de una instancia OpenCTI real (el Hub arranca standalone, sin conexión a OpenCTI configurada). Cubre: login (redirect sin sesión, token válido, token inválido), descartar una entrega de DLQ con motivo obligatorio, y el flujo completo de políticas (crear borrador → simular con el error esperado sin OpenCTI real → publicar con motivo).

## 4) Secciones de navegación

Overview, Observabilidad & Logs, Operaciones & DLQ, Políticas, Destinos, OpenCTI/Ingesta, Auditoría & Configuración — más **Conexión OpenCTI**, para configurar URL/token/TLS/stream_id en caliente. RBAC del lado del cliente (misma jerarquía que [docs/API-ADMIN.md](API-ADMIN.md) §2) oculta botones de acciones que igual fallarían con 403 en el servidor — la autorización real siempre la aplica el Admin API.

Ver [docs/ESTADO.md](ESTADO.md) para lo que la propia UI marca como "no disponible" (visor de logs en vivo, configuración operativa editable, preview de volumen al rebobinar el cursor).
