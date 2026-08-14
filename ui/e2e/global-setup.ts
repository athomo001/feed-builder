import { ChildProcess, spawn } from 'node:child_process';
import { writeFileSync } from 'node:fs';
import path from 'node:path';

// Arranca el Admin API (hub.api) con estado pre-sembrado para los tests E2E.
// No usamos Playwright `webServer` para esto porque el backend necesita
// sembrarse (token + una entrega dead-letter) ANTES de aceptar requests, y
// `webServer` no tiene un hook para eso -- ver ui/e2e/seed_and_run_backend.py.
const PID_FILE = path.join(__dirname, '.backend.pid');
const READY_TIMEOUT_MS = 30_000;

function waitForBackend(child: ChildProcess): Promise<void> {
  return new Promise((resolve, reject) => {
    let seeded = false;
    const start = Date.now();

    child.stdout?.on('data', (chunk: Buffer) => {
      const text = chunk.toString();
      process.stdout.write(`[backend] ${text}`);
      if (text.includes('E2E_SEED_READY')) {
        seeded = true;
      }
    });
    child.stderr?.on('data', (chunk: Buffer) => process.stderr.write(`[backend] ${chunk.toString()}`));
    child.on('exit', (code) => {
      if (!seeded) {
        reject(new Error(`backend process exited early (code ${code}) before seeding finished`));
      }
    });

    const poll = setInterval(async () => {
      if (Date.now() - start > READY_TIMEOUT_MS) {
        clearInterval(poll);
        reject(new Error('timed out waiting for the E2E backend to become ready'));
        return;
      }
      if (!seeded) return;
      try {
        const res = await fetch('http://127.0.0.1:8000/healthz/liveness');
        if (res.ok) {
          clearInterval(poll);
          resolve();
        }
      } catch {
        // el server todavia no acepta conexiones, seguir esperando
      }
    }, 300);
  });
}

export default async function globalSetup(): Promise<void> {
  const child = spawn('python', ['seed_and_run_backend.py'], {
    cwd: __dirname,
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  if (child.pid) {
    writeFileSync(PID_FILE, String(child.pid));
  }
  await waitForBackend(child);
}
