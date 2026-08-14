import { defineConfig, devices } from '@playwright/test';

// spec/07-ADMIN-UI-ANGULAR.md "Playwright para End-to-End, enfocado en:
// flujos de autenticacion, simulador de politicas, gestion de DLQ,
// navegacion por teclado, estados degradados y diseno responsive".
export default defineConfig({
  testDir: './e2e/tests',
  timeout: 30_000,
  retries: 0,
  fullyParallel: false,
  workers: 1,
  globalSetup: require.resolve('./e2e/global-setup.ts'),
  globalTeardown: require.resolve('./e2e/global-teardown.ts'),
  use: {
    baseURL: 'http://localhost:4200',
    trace: 'retain-on-failure',
  },
  webServer: {
    command: 'npx ng serve --port 4200',
    url: 'http://localhost:4200',
    reuseExistingServer: !process.env['CI'],
    timeout: 120_000,
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
});
