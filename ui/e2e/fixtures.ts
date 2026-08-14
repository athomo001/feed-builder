import { readFileSync } from 'node:fs';
import path from 'node:path';
import { Page } from '@playwright/test';

export function getSeedToken(): string {
  const raw = readFileSync(path.join(__dirname, '.auth-token.json'), 'utf-8');
  return (JSON.parse(raw) as { token: string }).token;
}

export async function login(page: Page, role = 'security-admin'): Promise<void> {
  await page.goto('/login');
  await page.getByLabel('API token').fill(getSeedToken());
  await page.getByLabel('Rol del token').click();
  await page.getByRole('option', { name: role, exact: true }).click();
  await page.getByRole('button', { name: 'Entrar' }).click();
  await page.waitForURL(/\/overview/);
}
