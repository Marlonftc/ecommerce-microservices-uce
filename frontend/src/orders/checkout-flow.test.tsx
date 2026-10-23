// @vitest-environment jsdom
// Checkout with the real routes, session and cart; fetch answers like order-service
// behind the gateway. Covers keys, 202 RESERVING retries, uncertain outcomes and reloads.
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AppRoutes } from '../App';
import { AuthProvider } from '../auth/AuthContext';
import { createFakeApi, deferred, json, type FakeApi } from '../test/fakeApi';
import { ORDER_ID, OTHER_ORDER_ID, order } from '../test/orders';
import { cart, otherCustomer, signIn } from '../test/shop';
import { PENDING_KEY, getCheckout, saveCheckout } from './pendingOps';

let api: FakeApi;

const LINES = [
  { id: 1, quantity: 2, price: '749.99', name: 'Laptop 14 pulgadas' },
  { id: 5, quantity: 1, price: '19.50', name: 'Mouse inalámbrico' },
];

function Where() {
  const location = useLocation();
  return <output data-testid="where">{location.pathname}</output>;
}

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <AuthProvider>
        <AppRoutes />
        <Where />
      </AuthProvider>
    </MemoryRouter>,
  );
}

const where = () => screen.getByTestId('where').textContent;
const posts = () => api.callsTo('POST /api/orders');
const keyOf = (i: number) => (posts()[i]!.init?.headers as Record<string, string>)['Idempotency-Key'];
const confirmButton = () => screen.getByRole('button', { name: 'Confirmar pedido' }) as HTMLButtonElement;
const conflict = (code: string, extra: Record<string, unknown> = {}) => json(409, { detail: { code, message: 'x', ...extra } });

async function ready() {
  await waitFor(() => expect(confirmButton().disabled).toBe(false));
}

beforeEach(() => {
  api = createFakeApi();
  vi.stubGlobal('fetch', api.fetchMock);
  sessionStorage.clear();
  signIn(api);
  api.on('GET', '/api/cart', () => json(200, cart(5, LINES)));
  api.on('GET', `/api/orders/${ORDER_ID}`, () => json(200, order()));
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('checkout', () => {
  it('one POST with a saved key and the reviewed cart_version; then the order page', async () => {
    const answer = deferred();
    api.on('POST', '/api/orders', () => answer.promise);
    renderAt('/checkout');
    await ready();
    expect(screen.getByText('$1.519,48')).toBeTruthy(); // estimate from the cart
    const readsBefore = api.callsTo('GET /api/cart').length;

    const button = confirmButton();
    fireEvent.click(button);
    fireEvent.click(button); // double click: still one request
    await waitFor(() => expect(posts()).toHaveLength(1));
    expect(posts()[0]!.body).toEqual({ cart_version: 5 });
    expect(keyOf(0)).toMatch(/^[0-9a-f-]{36}$/);
    expect(getCheckout(7)?.key).toBe(keyOf(0)); // saved BEFORE the answer

    answer.resolve(json(201, order()));
    expect(await screen.findByRole('heading', { name: 'Pedido 0B9A6C1E' })).toBeTruthy();
    expect(where()).toBe(`/pedidos/${ORDER_ID}`);
    expect(posts()).toHaveLength(1);
    expect(getCheckout(7)).toBeNull(); // result known: the attempt is removed
    expect(api.callsTo('GET /api/cart').length).toBeGreaterThan(readsBefore); // cart now locked
  });

  it('202 RESERVING: the same POST after 2, 4 and 8 s, never two at once, until it leaves RESERVING', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const slow = deferred();
    api.once('POST', '/api/orders', () => json(202, order({ status: 'RESERVING', expires_at: null })));
    api.once('POST', '/api/orders', () => slow.promise);
    api.on('POST', '/api/orders', () => json(201, order()));
    renderAt('/checkout');
    await ready();
    fireEvent.click(confirmButton());
    expect(await screen.findByText('Reservando tus productos')).toBeTruthy();
    expect(getCheckout(7)?.orderId).toBe(ORDER_ID);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(posts()).toHaveLength(2);
    // The second POST has not answered: time passes, no other POST of the same attempt
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    expect(posts()).toHaveLength(2);

    slow.resolve(json(202, order({ status: 'RESERVING', expires_at: null })));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(4000);
    });
    expect(await screen.findByRole('heading', { name: 'Pedido 0B9A6C1E' })).toBeTruthy();
    expect(posts()).toHaveLength(3);
    expect(new Set([keyOf(0), keyOf(1), keyOf(2)]).size).toBe(1); // always the original key
    expect(posts().every((c) => (c.body as { cart_version: number }).cart_version === 5)).toBe(true);
  });

  it('RESERVING that never ends: manual retry with the same key after 3 retries', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    api.on('POST', '/api/orders', () => json(202, order({ status: 'RESERVING', expires_at: null })));
    renderAt('/checkout');
    await ready();
    fireEvent.click(confirmButton());
    for (const wait of [2000, 4000, 8000]) {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(wait);
      });
    }
    expect(await screen.findByText('El inventario no responde')).toBeTruthy();
    expect(posts()).toHaveLength(4);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
    });
    expect(posts()).toHaveLength(4); // no more automatic retries
    expect(screen.getByRole('link', { name: 'Ver el pedido' }).getAttribute('href')).toBe(`/pedidos/${ORDER_ID}`);

    fireEvent.click(screen.getByRole('button', { name: 'Reintentar' }));
    await waitFor(() => expect(posts()).toHaveLength(5));
    expect(keyOf(4)).toBe(keyOf(0));
  });

  it('leaving the page cancels the scheduled retries', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    api.on('POST', '/api/orders', () => json(202, order({ status: 'RESERVING', expires_at: null })));
    renderAt('/checkout');
    await ready();
    fireEvent.click(confirmButton());
    await screen.findByText('Reservando tus productos');
    cleanup();
    expect(vi.getTimerCount()).toBe(0); // no retry left scheduled
    await act(async () => {
      await vi.advanceTimersByTimeAsync(20_000);
    });
    expect(posts()).toHaveLength(1);
    expect(getCheckout(7)?.orderId).toBe(ORDER_ID); // kept, to recover it later
  });

  it('uncertain outcome + page reload: the attempt is recovered with its ORIGINAL key', async () => {
    api.once('POST', '/api/orders', () => Promise.reject(new TypeError('Failed to fetch')));
    api.on('POST', '/api/orders', () => json(200, order())); // the first one had created it
    renderAt('/checkout');
    await ready();
    fireEvent.click(confirmButton());
    expect(await screen.findByText('Comprobando tu pedido')).toBeTruthy();
    const originalKey = keyOf(0);
    expect(getCheckout(7)?.key).toBe(originalKey);

    cleanup(); // the user reloads the page before the automatic retry
    renderAt('/checkout');
    expect(await screen.findByRole('heading', { name: 'Pedido 0B9A6C1E' })).toBeTruthy();
    expect(posts()).toHaveLength(2);
    expect(keyOf(1)).toBe(originalKey); // never a new key for an unknown outcome
    expect(posts()[1]!.body).toEqual({ cart_version: 5 });
    expect(getCheckout(7)).toBeNull();
  });

  it('a gateway 504 is not "not processed": it is checked again with the same key', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    api.once('POST', '/api/orders', () => json(504, { detail: { code: 'UPSTREAM_TIMEOUT', message: 'x' } }));
    api.on('POST', '/api/orders', () => json(200, order()));
    renderAt('/checkout');
    await ready();
    fireEvent.click(confirmButton());
    await screen.findByText('Comprobando tu pedido');
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(await screen.findByRole('heading', { name: 'Pedido 0B9A6C1E' })).toBeTruthy();
    expect(keyOf(1)).toBe(keyOf(0));
  });

  it('CART_CHANGED: the cart is read again, the attempt is dropped, a new confirmation uses a new key', async () => {
    api.once('POST', '/api/orders', () => conflict('CART_CHANGED'));
    renderAt('/checkout');
    await ready();
    const reads = api.callsTo('GET /api/cart').length;
    api.on('GET', '/api/cart', () => json(200, cart(6, LINES)));
    fireEvent.click(confirmButton());
    expect(await screen.findByText(/Tu carrito cambió desde que lo revisaste/)).toBeTruthy();
    expect(api.callsTo('GET /api/cart').length).toBeGreaterThan(reads);
    expect(getCheckout(7)).toBeNull();

    api.on('POST', '/api/orders', () => json(201, order({ cart_version: 6 })));
    await ready();
    fireEvent.click(confirmButton());
    await screen.findByRole('heading', { name: 'Pedido 0B9A6C1E' });
    expect(posts()[1]!.body).toEqual({ cart_version: 6 });
    expect(keyOf(1)).not.toBe(keyOf(0));
  });

  it('CART_LOCKED: links to the user’s OWN active order, even when an admin sees others first', async () => {
    api.on('POST', '/api/orders', () => conflict('CART_LOCKED'));
    api.on('GET', '/api/orders', () =>
      json(200, { items: [order({ id: OTHER_ORDER_ID, user_id: 8 }), order({ status: 'PAYMENT_PENDING' })], total: 2, page: 1, size: 20 }),
    );
    renderAt('/checkout');
    await ready();
    fireEvent.click(confirmButton());
    expect(await screen.findByText(/Ya tienes una compra en curso/)).toBeTruthy();
    const link = await screen.findByRole('link', { name: 'Ver mi compra en curso' });
    expect(link.getAttribute('href')).toBe(`/pedidos/${ORDER_ID}`);
  });

  it('PRODUCT_NOT_AVAILABLE names the products; INSUFFICIENT_STOCK links to the cancelled order', async () => {
    api.once('POST', '/api/orders', () => conflict('PRODUCT_NOT_AVAILABLE', { product_ids: [5] }));
    api.once('POST', '/api/orders', () => conflict('INSUFFICIENT_STOCK', { product_id: 1, order_id: ORDER_ID }));
    renderAt('/checkout');
    await ready();
    fireEvent.click(confirmButton());
    expect(await screen.findByText('Productos: Mouse inalámbrico.')).toBeTruthy();

    await ready();
    fireEvent.click(confirmButton());
    expect(await screen.findByText(/No hay unidades suficientes de un producto/)).toBeTruthy();
    expect(screen.getByText('Productos: Laptop 14 pulgadas.')).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Ver el pedido cancelado' }).getAttribute('href')).toBe(`/pedidos/${ORDER_ID}`);
    expect(getCheckout(7)).toBeNull();
  });

  it('503 from order-service: no order was created, the attempt is dropped', async () => {
    api.on('POST', '/api/orders', () => json(503, { detail: 'product-service is not available, try again later' }));
    renderAt('/checkout');
    await ready();
    fireEvent.click(confirmButton());
    expect(await screen.findByText(/No se creó ningún pedido/)).toBeTruthy();
    expect(getCheckout(7)).toBeNull();
    expect(posts()).toHaveLength(1);
  });

  it('carts the backend would refuse cannot be confirmed', async () => {
    api.on('GET', '/api/cart', () => json(200, cart(5, [...LINES, { id: 9, quantity: 1, available: false }])));
    renderAt('/checkout');
    expect(await screen.findByText(/Quítalos del carrito para continuar/)).toBeTruthy();
    expect(confirmButton().disabled).toBe(true);
    cleanup();

    api.on('GET', '/api/cart', () => json(200, { ...cart(5, LINES), locked: true }));
    api.on('GET', '/api/orders', () => json(200, { items: [order()], total: 1, page: 1, size: 20 }));
    renderAt('/checkout');
    expect(await screen.findByRole('link', { name: 'Ver mi compra en curso' })).toBeTruthy();
    expect(confirmButton().disabled).toBe(true);
    expect(posts()).toHaveLength(0);
  });

  it('another user never recovers the saved attempt of the previous one', async () => {
    saveCheckout({ userId: 7, cartVersion: 5, key: 'ana-key', orderId: null });
    sessionStorage.removeItem('ecommerce-uce.session');
    signIn(api, otherCustomer);
    renderAt('/checkout');
    await ready();
    expect(posts()).toHaveLength(0); // no recovery POST for Luis
    expect(getCheckout(8)).toBeNull();
    expect(getCheckout(7)?.key).toBe('ana-key');
    expect(sessionStorage.getItem(PENDING_KEY)).not.toContain('token');
  });
});
