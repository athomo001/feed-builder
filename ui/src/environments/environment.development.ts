export const environment = {
  production: false,
  // python -m hub.api expone el Admin API en este host:puerto por defecto
  // (ver README.md 14.1). En produccion se sirve /admin/api/v1 detras del
  // mismo reverse proxy que esta UI (ver README.md 15.5), por eso
  // environment.ts (produccion) usa una ruta relativa en vez de este host.
  apiBaseUrl: 'http://localhost:8000/admin/api/v1',
};
