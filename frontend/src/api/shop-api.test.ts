// Requests built for product-service, inventory-service and the cart of order-service.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import * as cartApi from './cart';
import { setTokenProvider } from './http';
import { getStock } from './inventory';
import { listCategories, listProducts } from './products';

const fetchMock = vi.fn<typeof fetch>();
const ok = (body: unknown) =>
  new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });

function sent(index = 0) {
  const [url, init] = fetchMock.mock.calls[index]!;
  return { url: String(url), method: init?.method, body: init?.body ? JSON.parse(String(init.body)) : undefined };
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

describe('products', () => {
  it('list: search, category and page; inactive products are never requested', async () => {
    fetchMock.mockResolvedValueOnce(ok({ items: [], total: 0, limit: 12, offset: 24 }));
    await listProducts({ q: 'café', category: 'audio', limit: 12, offset: 24 });
    expect(sent().url).toBe('/api/products?q=caf%C3%A9&category=audio&limit=12&offset=24');
    expect(sent().url).not.toContain('include_inactive');
  });

  it('categories: one request when the catalogue has fewer than 100 products', async () => {
    fetchMock.mockResolvedValueOnce(
      ok({ items: [{ category: 'audio' }, { category: 'laptops' }, { category: 'audio' }], total: 3, limit: 100, offset: 0 }),
    );
    const scan = await listCategories();
    expect(scan).toEqual({ categories: ['audio', 'laptops'], complete: true, scanned: 3, total: 3 });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('categories: more than 500 products -> 5 requests at most and the list is marked incomplete', async () => {
    fetchMock.mockImplementation(async (input) => {
      const offset = Number(new URL(String(input), 'http://x').searchParams.get('offset'));
      const items = Array.from({ length: 100 }, (_, i) => ({ category: `cat-${(offset + i) % 7}` }));
      return ok({ items, total: 650, limit: 100, offset });
    });
    const scan = await listCategories();
    expect(fetchMock).toHaveBeenCalledTimes(5);
    expect(fetchMock.mock.calls.map(([url]) => new URL(String(url), 'http://x').searchParams.get('offset'))).toEqual([
      '0', '100', '200', '300', '400',
    ]);
    expect(scan.complete).toBe(false);
    expect(scan.scanned).toBe(500);
    expect(scan.total).toBe(650);
    expect(scan.categories).toHaveLength(7);
  });
});

describe('inventory', () => {
  it('one request for all the ids, without repetitions', async () => {
    fetchMock.mockResolvedValueOnce(ok([]));
    await getStock([3, 1, 3, 2]);
    expect(sent().url).toBe('/api/inventory?ids=3%2C1%2C2');
  });

  it('refuses 0 or more than 100 ids before calling the service', async () => {
    await expect(getStock([])).rejects.toThrow();
    await expect(getStock(Array.from({ length: 101 }, (_, i) => i + 1))).rejects.toThrow();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe('cart', () => {
  it('PUT sends the absolute quantity and expected_version in the JSON body', async () => {
    fetchMock.mockResolvedValueOnce(ok({ version: 5 }));
    await cartApi.setItem(7, 3, 4);
    expect(sent()).toEqual({ url: '/api/cart/items/7', method: 'PUT', body: { quantity: 3, expected_version: 4 } });
  });

  it('DELETE of an item and of the cart send expected_version in the query', async () => {
    fetchMock.mockImplementation(async () => ok({ version: 6 }));
    await cartApi.removeItem(7, 5);
    await cartApi.clearCart(6);
    expect(sent(0)).toEqual({ url: '/api/cart/items/7?expected_version=5', method: 'DELETE', body: undefined });
    expect(sent(1)).toEqual({ url: '/api/cart?expected_version=6', method: 'DELETE', body: undefined });
  });

  it('expected_version 0 (a cart never used) is still sent', async () => {
    fetchMock.mockResolvedValueOnce(ok({ version: 0 }));
    await cartApi.clearCart(0);
    expect(sent().url).toBe('/api/cart?expected_version=0');
  });

  it('invalid product ids never reach the network', async () => {
    for (const id of [0, -1, 1.5, 2147483648]) {
      await expect(cartApi.setItem(id, 1, 0)).rejects.toThrow();
    }
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
