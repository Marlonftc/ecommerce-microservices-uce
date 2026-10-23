import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from './errors';
import { buildUrl, http, request, setTokenProvider, setUnauthorizedHandler } from './http';

function jsonResponse(status: number, body: unknown, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json', ...headers },
  });
}

const fetchMock = vi.fn<typeof fetch>();

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal('fetch', fetchMock);
  setTokenProvider(() => null);
  setUnauthorizedHandler(null);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

async function failure(promise: Promise<unknown>): Promise<ApiError> {
  try {
    await promise;
  } catch (error) {
    if (error instanceof ApiError) return error;
    throw error;
  }
  throw new Error('the request did not fail');
}

describe('URLs', () => {
  it('always uses the relative /api prefix and encodes the query', () => {
    expect(buildUrl('/products', { q: 'café', limit: 5, empty: '', none: undefined, nil: null })).toBe(
      '/api/products?q=caf%C3%A9&limit=5',
    );
    expect(buildUrl('/cart')).toBe('/api/cart');
  });

  it('rejects absolute and protocol-relative URLs', () => {
    expect(() => buildUrl('http://product-service:8000/products')).toThrow();
    expect(() => buildUrl('//evil.example/x')).toThrow();
    expect(() => buildUrl('products')).toThrow();
  });
});

describe('successful responses', () => {
  it('returns JSON data, status and X-Request-ID', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, { items: [], total: 0, limit: 20, offset: 0 }, { 'X-Request-ID': 'abc123def456' }));
    const result = await http.get<{ total: number }>('/products', { query: { limit: 20 } });
    expect(result).toEqual({ data: { items: [], total: 0, limit: 20, offset: 0 }, status: 200, requestId: 'abc123def456' });
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toBe('/api/products?limit=20');
    expect(init?.method).toBe('GET');
  });

  it('accepts 204 without a body', async () => {
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }));
    const result = await http.delete('/products/7');
    expect(result.status).toBe(204);
    expect(result.data).toBeUndefined();
  });

  it('keeps 202 as a valid answer (e.g. PAYMENT_PENDING)', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(202, { status: 'PAYMENT_PENDING' }));
    const result = await http.post<{ status: string }>('/orders/x/pay', { simulation: 'SLOW_APPROVE' });
    expect(result.status).toBe(202);
    expect(result.data.status).toBe('PAYMENT_PENDING');
  });

  it('sends JSON bodies and extra headers such as Idempotency-Key', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(201, { id: 'o1' }));
    await http.post('/orders', { cart_version: 3 }, { headers: { 'Idempotency-Key': 'k-1' } });
    const init = fetchMock.mock.calls[0]![1]!;
    const headers = init.headers as Record<string, string>;
    expect(init.body).toBe('{"cart_version":3}');
    expect(headers['Content-Type']).toBe('application/json');
    expect(headers['Idempotency-Key']).toBe('k-1');
  });
});

describe('token', () => {
  it('adds the Authorization header from the token provider', async () => {
    setTokenProvider(() => 'jwt-value');
    fetchMock.mockResolvedValueOnce(jsonResponse(200, {}));
    await http.get('/users/me');
    expect((fetchMock.mock.calls[0]![1]!.headers as Record<string, string>).Authorization).toBe('Bearer jwt-value');
  });

  it('does not send it when auth is false (login, register)', async () => {
    setTokenProvider(() => 'jwt-value');
    fetchMock.mockResolvedValueOnce(jsonResponse(200, {}));
    await http.post('/auth/login', { email: 'a@b.c', password: 'x' }, { auth: false });
    expect((fetchMock.mock.calls[0]![1]!.headers as Record<string, string>).Authorization).toBeUndefined();
  });

  it('never lets the browser send X-Internal-Key', async () => {
    await expect(http.get('/products', { headers: { 'X-Internal-Key': 'x' } })).rejects.toThrow();
    await expect(http.get('/products', { headers: { 'x-internal-key': 'x' } })).rejects.toThrow();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe('HTTP errors', () => {
  it('422: FastAPI validation list becomes field issues', async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(422, {
        detail: [
          { loc: ['body', 'email'], msg: 'value is not a valid email address', type: 'value_error' },
          { loc: ['body', 'password'], msg: 'String should have at least 8 characters', type: 'string_too_short' },
        ],
      }, { 'X-Request-ID': 'req-422-abcdef' }),
    );
    const error = await failure(http.post('/auth/register', {}, { auth: false }));
    expect(error.kind).toBe('http');
    expect(error.status).toBe(422);
    expect(error.message).toBe('Revisa los datos ingresados.');
    expect(error.issues).toEqual([
      { field: 'email', message: 'value is not a valid email address' },
      { field: 'password', message: 'String should have at least 8 characters' },
    ]);
    expect(error.requestId).toBe('req-422-abcdef');
  });

  it('404: FastAPI text detail is kept for diagnosis', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(404, { detail: 'Product not found' }));
    const error = await failure(http.get('/products/999'));
    expect([error.status, error.detail, error.code]).toEqual([404, 'Product not found', null]);
    expect(error.message).toBe('No se encontró el recurso solicitado.');
  });

  it('409: business code of order-service', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(409, { detail: { code: 'CART_LOCKED', message: 'A checkout is in progress' } }));
    const error = await failure(http.put('/cart/items/1', { quantity: 2 }));
    expect([error.status, error.code]).toEqual([409, 'CART_LOCKED']);
    expect(error.message).toContain('compra en curso');
  });

  it('429: gateway JSON with Retry-After', async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(429, { detail: { code: 'TOO_MANY_REQUESTS', message: 'Too many login attempts, try again later' } }, { 'Retry-After': '6' }),
    );
    const error = await failure(http.post('/auth/login', {}, { auth: false }));
    expect([error.status, error.code, error.retryAfter]).toEqual([429, 'TOO_MANY_REQUESTS', 6]);
  });

  it.each([
    [502, 'UPSTREAM_UNAVAILABLE', 'no está disponible'],
    [503, null, 'no está disponible'],
    [504, 'UPSTREAM_TIMEOUT', 'tardó demasiado'],
  ])('%i: gateway or service unavailable', async (status, code, text) => {
    const body = code ? { detail: { code, message: 'x' } } : { detail: 'product-service is not available, try again later' };
    fetchMock.mockResolvedValueOnce(jsonResponse(status, body, { 'X-Request-ID': `gw-${status}-abcdef` }));
    const error = await failure(http.get('/notifications'));
    expect([error.kind, error.status, error.code]).toEqual(['http', status, code]);
    expect(error.message).toContain(text);
    expect(error.requestId).toBe(`gw-${status}-abcdef`);
  });

  it('non-JSON error bodies (e.g. an HTML page) do not break the client', async () => {
    fetchMock.mockResolvedValueOnce(new Response('<html>Bad Gateway</html>', { status: 502, headers: { 'Content-Type': 'text/html' } }));
    const error = await failure(http.get('/products'));
    expect([error.status, error.code]).toEqual([502, null]);
    expect(error.detail).toContain('Bad Gateway');
  });
});

describe('network failures', () => {
  it('connection error becomes kind "network"', async () => {
    fetchMock.mockRejectedValueOnce(new TypeError('Failed to fetch'));
    const error = await failure(http.get('/products'));
    expect([error.kind, error.status]).toEqual(['network', 0]);
  });

  it('a request that does not answer in time becomes kind "timeout"', async () => {
    fetchMock.mockImplementationOnce((_url, init) =>
      new Promise((_resolve, reject) => {
        init?.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')));
      }),
    );
    const error = await failure(request('/orders', { method: 'POST', body: {}, timeoutMs: 20 }));
    expect([error.kind, error.status]).toEqual(['timeout', 0]);
    expect(error.message).toContain('revisa su estado');
  });

  it('a request cancelled by the caller is not reported as an API error', async () => {
    const controller = new AbortController();
    fetchMock.mockImplementationOnce((_url, init) =>
      new Promise((_resolve, reject) => {
        init?.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')));
      }),
    );
    const pending = http.get('/orders', { signal: controller.signal });
    controller.abort();
    await expect(pending).rejects.toMatchObject({ name: 'AbortError' });
  });
});

describe('unauthorized handler', () => {
  it('receives the token that the failed request carried', async () => {
    const handler = vi.fn();
    setUnauthorizedHandler(handler);
    setTokenProvider(() => 'token-A');
    fetchMock.mockResolvedValueOnce(jsonResponse(401, { detail: 'Invalid or expired token' }));
    const error = await failure(http.get('/users/me'));
    expect(handler).toHaveBeenCalledWith(error, 'token-A');
  });

  it('is not called for requests sent without a token (login: wrong credentials)', async () => {
    const handler = vi.fn();
    setUnauthorizedHandler(handler);
    setTokenProvider(() => 'token-A');
    fetchMock.mockResolvedValueOnce(jsonResponse(401, { detail: 'Invalid credentials' }));
    await failure(http.post('/auth/login', {}, { auth: false }));
    expect(handler).not.toHaveBeenCalled();
  });

  it('is not called for other errors', async () => {
    const handler = vi.fn();
    setUnauthorizedHandler(handler);
    setTokenProvider(() => 'token-A');
    fetchMock.mockResolvedValueOnce(jsonResponse(403, { detail: 'Admin role required' }));
    await failure(http.get('/users'));
    expect(handler).not.toHaveBeenCalled();
  });
});
