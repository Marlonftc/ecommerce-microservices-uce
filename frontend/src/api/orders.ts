// order-service orders through the gateway: /api/orders. Every call needs the token.
// POST /orders and POST /orders/{id}/pay carry an Idempotency-Key: repeating the same
// request with the same key never creates a second order or a second payment.
import { http } from './http';
import type { NumberedPage, OrderOut, Simulation } from './types';

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function isOrderId(value: string | undefined | null): value is string {
  return typeof value === 'string' && UUID.test(value);
}

function orderPath(id: string, suffix = ''): string {
  if (!isOrderId(id)) throw new Error('order ids are UUIDs');
  return `/orders/${id}${suffix}`;
}

function keyHeader(key: string): Record<string, string> {
  if (key.length < 1 || key.length > 100) throw new Error('Idempotency-Key must have 1 to 100 characters');
  return { 'Idempotency-Key': key };
}

/** Answer of POST /orders and POST /orders/{id}/pay: the status code matters (201/200/202) */
export interface OrderAnswer {
  order: OrderOut;
  status: number;
}

/** POST /api/orders: 201 new, 200 same key, 202 RESERVING (repeat with the same key) */
export async function createOrder(cartVersion: number, key: string, signal?: AbortSignal): Promise<OrderAnswer> {
  const response = await http.post<OrderOut>('/orders', { cart_version: cartVersion }, { headers: keyHeader(key), signal });
  return { order: response.data, status: response.status };
}

/** GET /api/orders?page&size: own orders; an admin gets the orders of every customer */
export async function listOrders(page: number, size: number, signal?: AbortSignal): Promise<NumberedPage<OrderOut>> {
  return (await http.get<NumberedPage<OrderOut>>('/orders', { query: { page, size }, signal })).data;
}

/** GET /api/orders/{id}: 404 also when the order belongs to someone else */
export async function getOrder(id: string, signal?: AbortSignal): Promise<OrderOut> {
  return (await http.get<OrderOut>(orderPath(id), { signal })).data;
}

/** POST /api/orders/{id}/pay: 200 final result, 202 PAYMENT_PENDING (repeat with the same key and mode) */
export async function payOrder(id: string, simulation: Simulation, key: string, signal?: AbortSignal): Promise<OrderAnswer> {
  const response = await http.post<OrderOut>(orderPath(id, '/pay'), { simulation }, { headers: keyHeader(key), signal });
  return { order: response.data, status: response.status };
}

/** POST /api/orders/{id}/cancel: only from PENDING_PAYMENT; repeating it returns the cancelled order */
export async function cancelOrder(id: string, signal?: AbortSignal): Promise<OrderOut> {
  return (await http.post<OrderOut>(orderPath(id, '/cancel'), undefined, { signal })).data;
}
