import { existsSync, readFileSync, unlinkSync } from 'node:fs';
import path from 'node:path';

const PID_FILE = path.join(__dirname, '.backend.pid');

export default async function globalTeardown(): Promise<void> {
  if (!existsSync(PID_FILE)) return;
  const pid = Number(readFileSync(PID_FILE, 'utf-8'));
  try {
    process.kill(pid);
  } catch {
    // ya estaba cerrado
  }
  unlinkSync(PID_FILE);
}
