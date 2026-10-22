// HTTP client of the web app. Every call goes to the relative path /api on the same
// origin, so the browser talks only to the gateway; no backend address is compiled in.
// Nothing is logged: request bodies (passwords) and tokens never reach the console.
import { ApiError } from './errors';

export const API_PREFIX = '/api';
export const DEFAULT_TIMEOUT_MS = 15_000;

export type QueryValue = string | number | boolean | null | undefined;
export type HttpMethod = 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';

export interface RequestOptions {
  method?: HttpMethod;
  query?: Record<string, QueryValue>;
  /** Sent as JSON when present */
  body?: unknown;
  /** Extra headers, e.g. Idempotency-Key */
  headers?: Record<string, string>;
  timeoutMs?: number;
  /** Cancels the request (e.g. when a page is left); the promise then rejects with AbortError */
  signal?: AbortSignal;
  /** false: do not send the token even if there is a session (login, register) */
  auth?: boolean;
}

export interface ApiResponse<T> {
  data: T;
  status: number;
  requestId: string | null;
}

// --- Session hooks (filled in by the authentication module) ----------------------------
type TokenProvider = () => string | null;
let tokenProvider: TokenProvider = () => null;

/** The auth module registers here how to read the current JWT. */
export function setTokenProvider(provider: TokenProvider): void {
  tokenProvider = provider;
}

/**
 * Called when a request that carried a token gets 401. It receives the token that
 * request used, so the session module can ignore answers to an older session.
 * Requests sent without a token (login, register) never trigger it.
 */
type UnauthorizedHandler = (error: ApiError, sentToken: string) => void;
let unauthorizedHandler: UnauthorizedHandler | null = null;

export function setUnauthorizedHandler(handler: UnauthorizedHandler | null): void {
  unauthorizedHandler = handler;
}

// Only the services add this header between themselves; the browser must never send it
const FORBIDDEN_HEADERS = new Set(['x-internal-key']);

export function buildUrl(path: string, query?: Record<string, QueryValue>): string {
  if (!path.startsWith('/') || path.startsWith('//')) {
    throw new Error('API paths must be relative and start with "/" (e.g. "/products")');
  }
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value !== undefined && value !== null && value !== '') params.append(key, String(value));
  }
  const qs = params.toString();
  return `${API_PREFIX}${path}${qs ? `?${qs}` : ''}`;
}

async function readBody(response: Response): Promise<unknown> {
  if (response.status === 204 || response.status === 205) return undefined;
  const text = await response.text();
  if (!text) return undefined;
  const type = response.headers.get('Content-Type') ?? '';
  if (type.includes('json')) {
    try {
      return JSON.parse(text);
    } catch {
      return text;
    }
  }
  return text;
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<ApiResponse<T>> {
  const { method = 'GET', query, body, timeoutMs = DEFAULT_TIMEOUT_MS, signal, auth = true } = options;

  const headers: Record<string, string> = { Accept: 'application/json' };
  for (const [name, value] of Object.entries(options.headers ?? {})) {
    if (FORBIDDEN_HEADERS.has(name.toLowerCase())) {
      throw new Error(`The header ${name} cannot be sent from the browser`);
    }
    headers[name] = value;
  }
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  const sentToken = auth ? tokenProvider() : null;
  if (sentToken) headers.Authorization = `Bearer ${sentToken}`;

  const controller = new AbortController();
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);
  const onExternalAbort = () => controller.abort();
  if (signal) {
    if (signal.aborted) controller.abort();
    else signal.addEventListener('abort', onExternalAbort, { once: true });
  }

  let response: Response;
  try {
    response = await fetch(buildUrl(path, query), {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: controller.signal,
      credentials: 'same-origin',
    });
  } catch (error) {
    if (timedOut) throw ApiError.timeout();
    if (signal?.aborted) throw error; // cancelled by the caller: not an API failure
    throw ApiError.network();
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener('abort', onExternalAbort);
  }

  const requestId = response.headers.get('X-Request-ID');
  let data: unknown;
  try {
    data = await readBody(response);
  } catch {
    if (timedOut) throw ApiError.timeout();
    throw ApiError.network();
  }
  if (!response.ok) {
    const error = ApiError.fromResponse(response.status, data, requestId, response.headers.get('Retry-After'));
    if (response.status === 401 && sentToken && unauthorizedHandler) unauthorizedHandler(error, sentToken);
    throw error;
  }
  return { data: data as T, status: response.status, requestId };
}

export const http = {
  get: <T>(path: string, options?: Omit<RequestOptions, 'method' | 'body'>) =>
    request<T>(path, { ...options, method: 'GET' }),
  post: <T>(path: string, body?: unknown, options?: Omit<RequestOptions, 'method' | 'body'>) =>
    request<T>(path, { ...options, method: 'POST', body }),
  put: <T>(path: string, body?: unknown, options?: Omit<RequestOptions, 'method' | 'body'>) =>
    request<T>(path, { ...options, method: 'PUT', body }),
  patch: <T>(path: string, body?: unknown, options?: Omit<RequestOptions, 'method' | 'body'>) =>
    request<T>(path, { ...options, method: 'PATCH', body }),
  delete: <T>(path: string, options?: Omit<RequestOptions, 'method' | 'body'>) =>
    request<T>(path, { ...options, method: 'DELETE' }),
};
