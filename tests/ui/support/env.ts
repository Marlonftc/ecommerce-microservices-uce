// Configuration of the UI tests, read from the root .env of the project (the same
// file Docker Compose uses). Only the three values the tests need are taken; the
// file is never copied, printed or written anywhere.
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

export const UI_DIR = resolve(dirname(fileURLToPath(import.meta.url)), '..');
export const ROOT = resolve(UI_DIR, '..', '..');

/** Minimal reader for KEY=VALUE lines (comments and blank lines skipped, optional quotes) */
function readDotEnv(path: string): Record<string, string> {
  let text = '';
  try {
    text = readFileSync(path, 'utf8');
  } catch {
    return {};
  }
  const values: Record<string, string> = {};
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith('#')) continue;
    const eq = line.indexOf('=');
    if (eq < 1) continue;
    const key = line.slice(0, eq).trim();
    let value = line.slice(eq + 1).trim();
    if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
      value = value.slice(1, -1);
    }
    values[key] = value;
  }
  return values;
}

const dotEnv = readDotEnv(resolve(ROOT, '.env'));
const pick = (key: string): string | undefined => process.env[key] || dotEnv[key] || undefined;

export const GATEWAY_URL = `http://127.0.0.1:${pick('GATEWAY_PORT') ?? '8080'}`;

/** Admin account created by user-migrate; never logged or stored */
export function adminCredentials(): { email: string; password: string } {
  const email = pick('ADMIN_EMAIL');
  const password = pick('ADMIN_PASSWORD');
  if (!email || !password) throw new Error('ADMIN_EMAIL and ADMIN_PASSWORD must be set in the root .env');
  return { email, password };
}
