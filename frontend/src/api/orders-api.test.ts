import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from './errors';
import { setTokenProvider } from './http';
import { cancelOrder, createOrder, getOrder, listOrders, payOrder } from './orders';

const ID = '0b9a6c1e-4f2d-4c8a-9e3b-7d5f1a2b3c4d';
const fetchMock = vi.fn<typeof fetch>();
const reply = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

function sent(i = 0) {
  const [url, init] = fetchMock.mock.calls[i]!;
  return {
    url: String(url),
    method: init?.method,
    headers: init?.headers as Record<string, string>,
    body: init?.body ? JSON.parse(String(init.body)) : undefined,
  };
}

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal('fetch', fetchMock);
  setTokenProvider(() => 'jwt');
});
afterEach(() => {
  vi.unstubAllGlobals();
  setTokenProvider(() => null);
});

describe('orders API', () => {
  it('POST /orders: Idempotency-Key header, cart_version body, and the status code is kept (202)', async () => {
    fetchMock.mockResolvedValueOnce(reply(202, { id: ID, status: 'RESERVING' }));
    const answer = await createOrder(5, 'key-1');
    expect(answer.status).toBe(202);
    expect(sent()).toMatchObject({ url: '/api/orders', method: 'POST', body: { cart_version: 5 } });
    expect(sent().headers['Idempotency-Key']).toBe('key-1');
    expect(sent().headers.Authorization).toBe('Bearer jwt');
  });

  it('POST /orders/{id}/pay: simulation in the body, the same key header', async () => {
    fetchMock.mockResolvedValueOnce(reply(200, { id: ID, status: 'PAID' }));
    await payOrder(ID, 'SLOW_APPROVE', 'pay-key');
    expect(sent()).toMatchObject({ url: `/api/orders/${ID}/pay`, method: 'POST', body: { simulation: 'SLOW_APPROVE' } });
    expect(sent().headers['Idempotency-Key']).toBe('pay-key');
  });

  it('cancel without body; list and detail', async () => {
    fetchMock.mockImplementation(async () => reply(200, { items: [], total: 0, page: 2, size: 10 }));
    await cancelOrder(ID);
    await listOrders(2, 10);
    await getOrder(ID);
    expect(sent(0)).toMatchObject({ url: `/api/orders/${ID}/cancel`, method: 'POST', body: undefined });
    expect(sent(1).url).toBe('/api/orders?page=2&size=10');
    expect(sent(2)).toMatchObject({ url: `/api/orders/${ID}`, method: 'GET' });
  });

  it('invalid ids and keys never reach the network', async () => {
    await expect(getOrder('../internal/x')).rejects.toThrow();
    await expect(payOrder('123', 'APPROVE', 'k')).rejects.toThrow();
    await expect(createOrder(1, '')).rejects.toThrow();
    await expect(createOrder(1, 'x'.repeat(101))).rejects.toThrow();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe('ApiError.extra', () => {
  it('keeps the extra fields of a coded error, without changing code or message', () => {
    const error = ApiError.fromResponse(409, {
      detail: { code: 'INSUFFICIENT_STOCK', message: 'Not enough units', product_id: 4, order_id: ID },
    }, 'r', null);
    expect(error.code).toBe('INSUFFICIENT_STOCK');
    expect(error.detail).toBe('Not enough units');
    expect(error.message).toBe('No hay existencias suficientes para completar el pedido.');
    expect(error.extra).toEqual({ product_id: 4, order_id: ID });
    expect(Object.isFrozen(error.extra)).toBe(true);
  });

  it('is empty for the other formats (text detail, validation list, non-JSON)', () => {
    expect(ApiError.fromResponse(404, { detail: 'Order not found' }, null, null).extra).toEqual({});
    expect(ApiError.fromResponse(422, { detail: [{ loc: ['body', 'x'], msg: 'bad' }] }, null, null).extra).toEqual({});
    expect(ApiError.fromResponse(502, '<html>', null, null).extra).toEqual({});
    expect(ApiError.network().extra).toEqual({});
  });
});
