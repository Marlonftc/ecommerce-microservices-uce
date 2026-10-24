// Calls to the public API through the gateway, used to prepare data and to check
// results. Every account and product created here gets the run prefix and is
// recorded in the run state, so global teardown can deactivate it.
import { randomBytes, randomUUID } from 'node:crypto';
import { setTimeout as sleep } from 'node:timers/promises';
import { expect, request, type APIRequestContext } from '@playwright/test';
import { adminCredentials, GATEWAY_URL } from './env';
import { readState, runId, updateState } from './state';

export interface Account {
  id: number;
  email: string;
  fullName: string;
  token: string;
}

export function newApi(): Promise<APIRequestContext> {
  return request.newContext({ baseURL: GATEWAY_URL });
}

export const auth = (token: string) => ({ Authorization: `Bearer ${token}` });

// --- login rate limit ---------------------------------------------------------------
// The gateway allows 10 login attempts per minute per address (burst 5, nodelay) and
// every request of this run comes from the same address. The same leaky bucket is
// simulated from the recorded attempts and a login waits until it has room to spare.
const RATE_MS = 6_000; // 10 per minute
const SAFE_EXCESS = 3; // nginx refuses above 5

/** Simulated excess of the bucket now (each attempt counts, even a refused one: safer) */
function currentExcess(times: number[], now: number): number {
  let excess = 0;
  let last: number | null = null;
  for (const t of times) {
    if (last !== null) excess = Math.max(0, excess - (t - last) / RATE_MS);
    excess += 1;
    last = t;
  }
  return last === null ? 0 : Math.max(0, excess - (now - last) / RATE_MS);
}

/** Waits until one more login fits in the gateway limit, then records it */
export async function reserveLogin(): Promise<void> {
  const times = readState().loginTimes;
  const excess = currentExcess(times, Date.now());
  if (excess > SAFE_EXCESS) await sleep(Math.ceil((excess - SAFE_EXCESS) * RATE_MS));
  updateState((s) => {
    s.loginTimes = [...s.loginTimes, Date.now()].slice(-30);
  });
}

/** POST /api/auth/login respecting the limit; a 429 waits Retry-After and tries again */
export async function login(api: APIRequestContext, email: string, password: string): Promise<string> {
  for (let attempt = 0; attempt < 5; attempt++) {
    await reserveLogin();
    const res = await api.post('/api/auth/login', { data: { email, password } });
    if (res.status() === 429) {
      await sleep((Number(res.headers()['retry-after']) || 6) * 1000 + 500);
      continue;
    }
    expect(res.status(), 'login').toBe(200);
    return ((await res.json()) as { access_token: string }).access_token;
  }
  throw new Error('login kept answering 429');
}

// --- accounts ----------------------------------------------------------------------
let adminToken: { token: string; at: number } | null = null;

/** Admin token, reused for 20 minutes inside one worker (tokens last 30) */
export async function adminLogin(api: APIRequestContext): Promise<string> {
  if (adminToken && Date.now() - adminToken.at < 20 * 60_000) return adminToken.token;
  const { email, password } = adminCredentials();
  adminToken = { token: await login(api, email, password), at: Date.now() };
  return adminToken.token;
}

let counter = 0;
export const uniqueSuffix = () => `${runId()}-${++counter}-${randomBytes(2).toString('hex')}`;

/** A new customer of this run; the random password lives only in this object */
export async function newCustomer(
  api: APIRequestContext,
  fullName = 'Cliente UI6',
): Promise<Account & { password: string }> {
  const email = `ui6-${uniqueSuffix()}@example.com`;
  const password = `Ui6-${randomBytes(12).toString('base64url')}`;
  const res = await api.post('/api/auth/register', { data: { email, full_name: fullName, password } });
  expect(res.status(), 'register').toBe(201);
  const { id } = (await res.json()) as { id: number };
  updateState((s) => s.userIds.push(id));
  return { id, email, fullName, password, token: await login(api, email, password) };
}

/** Records an account created through the interface, for cleanup */
export function recordUser(id: number): void {
  updateState((s) => s.userIds.push(id));
}

// --- products and stock ----------------------------------------------------------------
export interface Product {
  id: number;
  sku: string;
  name: string;
  price: string;
}

export async function newProduct(
  api: APIRequestContext,
  options: { label: string; stock: number | null; price?: string },
): Promise<Product> {
  const token = await adminLogin(api);
  const suffix = uniqueSuffix();
  const sku = `UI6-${suffix}`.toUpperCase();
  const name = `UI6 ${options.label} ${suffix}`;
  const price = options.price ?? '15.00';
  const res = await api.post('/api/products', {
    headers: auth(token),
    data: { sku, name, category: 'pruebas-ui', price, description: 'Producto de prueba de la etapa 6' },
  });
  expect(res.status(), 'create product').toBe(201);
  const { id } = (await res.json()) as { id: number };
  updateState((s) => s.productIds.push(id));
  if (options.stock !== null) await adjustStock(api, id, options.stock);
  return { id, sku, name, price };
}

export function recordProduct(id: number): void {
  updateState((s) => s.productIds.push(id));
}

export async function adjustStock(api: APIRequestContext, productId: number, delta: number): Promise<void> {
  const res = await api.post(`/api/inventory/${productId}/adjustments`, {
    headers: { ...auth(await adminLogin(api)), 'Idempotency-Key': randomUUID() },
    data: { delta, reason: 'Pruebas de interfaz etapa 6' },
  });
  expect(res.status(), 'stock adjustment').toBe(201);
}

export async function available(api: APIRequestContext, productId: number): Promise<number> {
  const res = await api.get(`/api/inventory/${productId}`, { headers: auth(await adminLogin(api)) });
  expect(res.status()).toBe(200);
  return ((await res.json()) as { available: number }).available;
}

// --- cart, orders, payments --------------------------------------------------------------
export async function putInCart(api: APIRequestContext, token: string, productId: number, quantity: number) {
  const res = await api.put(`/api/cart/items/${productId}`, { headers: auth(token), data: { quantity } });
  expect(res.status(), 'cart item').toBe(200);
  return (await res.json()) as { version: number };
}

export interface OrderOut {
  id: string;
  status: string;
  inventory_status: string;
  cancel_reason: string | null;
  user_id: number;
}

/** Order of the current cart, created through the API (the checkout page is tested elsewhere) */
export async function createOrder(api: APIRequestContext, token: string, productId: number): Promise<OrderOut> {
  const cart = await putInCart(api, token, productId, 1);
  const res = await api.post('/api/orders', {
    headers: { ...auth(token), 'Idempotency-Key': randomUUID() },
    data: { cart_version: cart.version },
  });
  expect(res.status(), 'create order').toBe(201);
  const order = (await res.json()) as OrderOut;
  expect(order.status).toBe('PENDING_PAYMENT');
  return order;
}

export async function payOrder(api: APIRequestContext, token: string, orderId: string): Promise<OrderOut> {
  const res = await api.post(`/api/orders/${orderId}/pay`, {
    headers: { ...auth(token), 'Idempotency-Key': randomUUID() },
    data: { simulation: 'APPROVE' },
  });
  expect(res.status(), 'pay order').toBe(200);
  return (await res.json()) as OrderOut;
}

export async function getOrder(api: APIRequestContext, token: string, orderId: string): Promise<OrderOut> {
  const res = await api.get(`/api/orders/${orderId}`, { headers: auth(token) });
  expect(res.status()).toBe(200);
  return (await res.json()) as OrderOut;
}

export async function unreadCount(api: APIRequestContext, token: string): Promise<number> {
  const res = await api.get('/api/notifications', { headers: auth(token), params: { unread_only: 'true', size: 1 } });
  expect(res.status()).toBe(200);
  return ((await res.json()) as { total: number }).total;
}
