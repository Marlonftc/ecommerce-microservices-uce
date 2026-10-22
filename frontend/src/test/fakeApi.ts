// Test helper: a fetch replacement that answers like the gateway, route by route.
// Answers can be delayed (deferred) to reproduce races between requests.
import { vi } from 'vitest';

export interface Call {
  key: string; // "PUT /api/cart/items/1"
  url: URL;
  init: RequestInit | undefined;
  body: unknown;
}

export type Handler = (call: Call) => Response | Promise<Response>;

export function json(status: number, body: unknown, headers: Record<string, string> = {}): Response {
  return new Response(body === undefined ? null : JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json', 'X-Request-ID': `test-${status}-req`, ...headers },
  });
}

/** A response the test releases later */
export interface Deferred {
  promise: Promise<Response>;
  resolve(response: Response): void;
  reject(error: unknown): void;
}

export function deferred(): Deferred {
  let resolve!: (response: Response) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<Response>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

/** Never answers; rejects like a real fetch when the request is aborted (timeout) */
export function hang(call: Call): Promise<Response> {
  return new Promise((_resolve, reject) => {
    call.init?.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')));
  });
}

export function createFakeApi() {
  const defaults = new Map<string, Handler>();
  const queued = new Map<string, Handler[]>();
  const calls: Call[] = [];

  const fetchMock = vi.fn(async (input: string, init?: RequestInit) => {
    const url = new URL(input, 'http://localhost');
    const key = `${init?.method ?? 'GET'} ${url.pathname}`;
    const body = typeof init?.body === 'string' ? JSON.parse(init.body) : undefined;
    const call: Call = { key, url, init, body };
    calls.push(call);
    // One-time answers first, in order; then the default answer of the route
    const handler = queued.get(key)?.shift() ?? defaults.get(key);
    if (!handler) return json(404, { detail: { code: 'NOT_FOUND', message: 'Route not found' } });
    return handler(call);
  });

  return {
    calls,
    fetchMock,
    /** Default answer of a route (replaces the previous default) */
    on(method: string, path: string, handler: Handler) {
      defaults.set(`${method} ${path}`, handler);
    },
    /** An answer used once, before the default one */
    once(method: string, path: string, handler: Handler) {
      const key = `${method} ${path}`;
      queued.set(key, [...(queued.get(key) ?? []), handler]);
    },
    callsTo(key: string): Call[] {
      return calls.filter((call) => call.key === key);
    },
  };
}

export type FakeApi = ReturnType<typeof createFakeApi>;
