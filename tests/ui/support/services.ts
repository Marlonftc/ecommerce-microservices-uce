// Stopping and starting microservices for the failure scenarios. Docker Compose is
// called from the host with the same files used to start the E2E system. A test
// that stops a service restores it in "finally", and global teardown starts every
// service recorded as stopped, so a failed test never leaves the system broken.
//
// UI_SERVICE_CONTROL (optional) replaces Docker Compose by another command, called as
// "<command> stop|start|wait <service>"; it is only meant for running the suite
// against a stack that is not in Docker.
import { execFileSync } from 'node:child_process';
import { setTimeout as sleep } from 'node:timers/promises';
import { GATEWAY_URL, ROOT } from './env';
import { updateState } from './state';

export type Service = 'user-service' | 'product-service' | 'inventory-service' | 'order-service'
  | 'payment-service' | 'notification-service';

const COMPOSE_FILES = ['-f', 'compose.yaml', '-f', 'compose.override.example.yaml', '-f', 'compose.e2e.yaml'];

/** A route of each service that answers without a token (401 or 200 from the service itself) */
const PROBES: Record<Service, string> = {
  'user-service': '/api/users/me',
  'product-service': '/api/products?limit=1',
  'inventory-service': '/api/inventory/1',
  'order-service': '/api/cart',
  'payment-service': '/api/payments',
  'notification-service': '/api/notifications',
};

function compose(...args: string[]): string {
  return execFileSync('docker', ['compose', ...COMPOSE_FILES, ...args], {
    cwd: ROOT,
    encoding: 'utf8',
    stdio: ['ignore', 'pipe', 'pipe'],
    timeout: 180_000,
  });
}

function control(action: 'stop' | 'start' | 'wait', service: Service): void {
  const custom = process.env.UI_SERVICE_CONTROL;
  if (custom) {
    execFileSync(custom, [action, service], { stdio: ['ignore', 'pipe', 'pipe'], timeout: 180_000 });
    return;
  }
  if (action !== 'wait') compose(action, service);
}

async function waitFor(check: () => Promise<boolean> | boolean, timeoutMs: number, what: string): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      if (await check()) return;
    } catch {
      // not ready yet
    }
    await sleep(1_000);
  }
  throw new Error(`timeout after ${timeoutMs / 1000}s waiting for ${what}`);
}

function healthyInDocker(service: Service): boolean {
  const out = compose('ps', '--format', 'json', service).trim();
  if (!out) return false;
  const rows: Array<{ State?: string; Health?: string }> = out.startsWith('[')
    ? JSON.parse(out)
    : out.split('\n').filter(Boolean).map((line) => JSON.parse(line));
  return rows.length > 0 && rows.every((r) => r.State === 'running' && r.Health === 'healthy');
}

/** True when the gateway reaches the service: any answer except its own 502/504 */
async function reachable(service: Service): Promise<boolean> {
  const res = await fetch(GATEWAY_URL + PROBES[service]);
  if (res.status !== 502 && res.status !== 504) return true;
  return false;
}

export async function stopService(service: Service): Promise<void> {
  updateState((s) => {
    if (!s.stoppedServices.includes(service)) s.stoppedServices.push(service);
  });
  control('stop', service);
  // The gateway answers 502/504 once the service is gone (it re-resolves the name every 10 s)
  await waitFor(async () => !(await reachable(service)), 60_000, `${service} to be unreachable`);
}

/** Starts the service, waits until it is healthy AND reachable through the gateway */
export async function startService(service: Service): Promise<void> {
  control('start', service);
  if (process.env.UI_SERVICE_CONTROL) control('wait', service);
  else await waitFor(() => healthyInDocker(service), 120_000, `${service} healthy`);
  await waitFor(() => reachable(service), 60_000, `${service} through the gateway`);
  updateState((s) => {
    s.stoppedServices = s.stoppedServices.filter((x) => x !== service);
  });
}

/** Runs body with the service stopped and always starts it again */
export async function withServiceStopped(service: Service, body: () => Promise<void>): Promise<void> {
  await stopService(service);
  try {
    await body();
  } finally {
    await startService(service);
  }
}
