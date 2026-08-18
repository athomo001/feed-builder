import { readFileSync } from 'node:fs';
import path from 'node:path';
import { Page } from '@playwright/test';

export function getSeedToken(): string {
  const raw = readFileSync(path.join(__dirname, '.auth-token.json'), 'utf-8');
  return (JSON.parse(raw) as { token: string }).token;
}

export async function login(page: Page, role = 'security-admin'): Promise<void> {
  // Desde el cambio del 2026-08-17 (AuthService.checkExistingSession entra
  // sola con un rol por defecto, ver hub/api/auth.py::require_role), la ruta
  // /login redirige de una via `redirectIfAuthenticated` -- ya no es
  // alcanzable con una sesion fresca. El equivalente soportado hoy es el
  // selector de rol (solo visual) en la barra superior del shell.
  await page.goto('/overview');
  await page.waitForURL(/\/overview/);
  // El aria-label del <mat-select> no llega al combobox accesible que
  // Angular Material arma (su nombre accesible es el texto de la opcion
  // elegida, ej. "security-admin") -- se lo ubica por rol en vez de label.
  await page.getByRole('combobox').click();
  await page.getByRole('option', { name: role, exact: true }).click();
}
