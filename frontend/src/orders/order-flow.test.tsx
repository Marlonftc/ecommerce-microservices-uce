// @vitest-environment jsdom
// Order page, simulated payment, cancellation and orders list with the real routes and
// session; fetch answers like order-service behind the gateway.
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AppRoutes } from '../App';
import { AuthProvider } from '../auth/AuthContext';
import { createFakeApi, deferred, json, type FakeApi } from '../test/fakeApi';
import { ORDER_ID, OTHER_ORDER_ID, order } from '../test/orders';
import { admin, cart, signIn } from '../test/shop';
import { getPayment, savePayment } from './pendingOps';
import { POLL_INTERVAL_MS, useOrderTracker, type OrderTracker } from './useOrderTracker';

let api: FakeApi;
const DETAIL = `/api/orders/${ORDER_ID}`;
const PAY = `/api/orders/${ORDER_ID}/pay`;

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <AuthProvider>
        <AppRoutes />
      </AuthProvider>
    </MemoryRouter>,
  );
}

const pays = () => api.callsTo(`POST ${PAY}`);
const payKey = (i: number) => (pays()[i]!.init?.headers as Record<string, string>)['Idempotency-Key'];
const payButton = () => screen.getByRole('button', { name: /Pagar|Reintentar pago/ }) as HTMLButtonElement;
const reads = () => api.callsTo(`GET ${DETAIL}`).length;
/** Build with VITE_PAYMENT_DEMO=true: open the demonstration options and pick one */
function chooseDemo(name: string | RegExp) {
  fireEvent.click(screen.getByText('Opciones de demostración'));
  fireEvent.click(screen.getByRole('radio', { name }));
}
const tick = (ms: number) => act(async () => {
  await vi.advanceTimersByTimeAsync(ms);
});

beforeEach(() => {
  api = createFakeApi();
  vi.stubGlobal('fetch', api.fetchMock);
  sessionStorage.clear();
  signIn(api);
  api.on('GET', '/api/cart', () => json(200, { ...cart(5, [{ id: 1, quantity: 2, price: '749.99' }]), locked: true }));
  api.on('GET', DETAIL, () => json(200, order()));
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe('order page', () => {
  it('shows the products with the prices fixed in the order, the total and the time left to pay', async () => {
    renderAt(`/pedidos/${ORDER_ID}`);
    expect(await screen.findByRole('heading', { name: 'Pedido 0B9A6C1E' })).toBeTruthy();
    expect(screen.getByText('Pendiente de pago')).toBeTruthy();
    const table = screen.getByRole('table');
    expect(within(table).getByText('Laptop 14 pulgadas')).toBeTruthy();
    expect(within(table).getByText('$1.519,48')).toBeTruthy();
    expect(screen.getByText(/para pagar; después la reserva se libera/)).toBeTruthy();
    expect(screen.getByText(/no procesa dinero real ni usa tarjetas/)).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Pagar $1.519,48' })).toBeTruthy();
  });

  it('normal build: a single "Pagar" that sends APPROVE explicitly; no demonstration options', async () => {
    // Set explicitly: Vite also reads VITE_* from the shell, so a VITE_PAYMENT_DEMO=true left in the
    // terminal must not turn this into a demonstration build (restored by unstubAllEnvs in afterEach)
    vi.stubEnv('VITE_PAYMENT_DEMO', '');
    api.on('POST', PAY, () => json(200, order({ status: 'PAID', inventory_status: 'CONFIRMED' })));
    renderAt(`/pedidos/${ORDER_ID}`);
    await screen.findByRole('heading', { name: 'Pedido 0B9A6C1E' });
    expect(screen.queryByText('Opciones de demostración')).toBeNull();
    expect(screen.queryAllByRole('radio')).toHaveLength(0);
    fireEvent.click(payButton());
    await screen.findByText('Pago aprobado. Tu carrito se vació.');
    expect(pays()[0]!.body).toEqual({ simulation: 'APPROVE' });
  });

  it('VITE_PAYMENT_DEMO=true: the three modes inside "Opciones de demostración", APPROVE by default', async () => {
    vi.stubEnv('VITE_PAYMENT_DEMO', 'true');
    renderAt(`/pedidos/${ORDER_ID}`);
    await screen.findByRole('heading', { name: 'Pedido 0B9A6C1E' });
    fireEvent.click(screen.getByText('Opciones de demostración'));
    expect((screen.getByRole('radio', { name: 'Aprobar el pago' }) as HTMLInputElement).checked).toBe(true);
    expect(screen.getByRole('radio', { name: 'Rechazar el pago' })).toBeTruthy();
    expect(screen.getByRole('radio', { name: /Aprobación lenta/ })).toBeTruthy();
    expect(screen.getByText(/no procesa dinero real/)).toBeTruthy();
  });

  it('only the exact value "true" enables the demonstration options', async () => {
    vi.stubEnv('VITE_PAYMENT_DEMO', 'TRUE ');
    renderAt(`/pedidos/${ORDER_ID}`);
    await screen.findByRole('heading', { name: 'Pedido 0B9A6C1E' });
    expect(screen.queryByText('Opciones de demostración')).toBeNull();
  });

  it('a saved REJECT attempt is repeated with REJECT even in a build without the demonstration options', async () => {
    vi.stubEnv('VITE_PAYMENT_DEMO', ''); // normal build, whatever the shell has
    savePayment({ userId: 7, orderId: ORDER_ID, key: 'demo-key', simulation: 'REJECT' });
    api.on('POST', PAY, () => json(200, order({ status: 'PAYMENT_REJECTED', inventory_status: 'RELEASED' })));
    renderAt(`/pedidos/${ORDER_ID}`);
    expect(await screen.findByText(/intento de pago sin confirmar.*\(rechazar el pago\)/)).toBeTruthy();
    expect(screen.queryByText('Opciones de demostración')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Reintentar pago' }));
    await screen.findByText(/El pago fue rechazado\. Tu carrito se conservó/);
    expect(pays()[0]!.body).toEqual({ simulation: 'REJECT' });
    expect(payKey(0)).toBe('demo-key');
  });

  it('an invalid id is "not found" without a request; a 404 says the order does not exist', async () => {
    renderAt('/pedidos/not-a-uuid');
    expect(await screen.findByRole('heading', { name: 'No encontramos esta página' })).toBeTruthy();
    expect(api.calls.some((c) => c.key.startsWith('GET /api/orders/'))).toBe(false);
    cleanup();
    api.on('GET', DETAIL, () => json(404, { detail: 'Order not found' }));
    renderAt(`/pedidos/${ORDER_ID}`);
    expect(await screen.findByRole('heading', { name: 'Pedido no encontrado' })).toBeTruthy();
  });

  it('APPROVE: PAID, the saved attempt is removed and the cart is read again (emptied)', async () => {
    api.on('POST', PAY, () => json(200, order({ status: 'PAID', inventory_status: 'CONFIRMED', payment_status: 'APPROVED' })));
    renderAt(`/pedidos/${ORDER_ID}`);
    await screen.findByRole('heading', { name: 'Pedido 0B9A6C1E' });
    expect(await screen.findByRole('link', { name: /Carrito, 2 unidades/ })).toBeTruthy();
    api.on('GET', '/api/cart', () => json(200, cart(6))); // the backend emptied it
    fireEvent.click(payButton());
    expect(await screen.findByText('Pago aprobado. Tu carrito se vació.')).toBeTruthy();
    expect(screen.getByText('Compra completada.')).toBeTruthy();
    expect(pays()[0]!.body).toEqual({ simulation: 'APPROVE' });
    expect(payKey(0)).toMatch(/^[0-9a-f-]{36}$/);
    expect(getPayment(7, ORDER_ID)).toBeNull();
    await waitFor(() => expect(screen.queryByRole('link', { name: /Carrito, 2/ })).toBeNull());
  });

  it('REJECT: PAYMENT_REJECTED, the cart is kept and the page offers to go back to it', async () => {
    api.on('POST', PAY, () => json(200, order({ status: 'PAYMENT_REJECTED', inventory_status: 'RELEASED', payment_status: 'REJECTED' })));
    api.on('GET', '/api/cart', () => json(200, cart(5, [{ id: 1, quantity: 2, price: '749.99' }])));
    vi.stubEnv('VITE_PAYMENT_DEMO', 'true');
    renderAt(`/pedidos/${ORDER_ID}`);
    await screen.findByRole('heading', { name: 'Pedido 0B9A6C1E' });
    chooseDemo('Rechazar el pago');
    fireEvent.click(payButton());
    expect(await screen.findByText(/El pago fue rechazado. Tu carrito se conservó/)).toBeTruthy();
    expect(pays()[0]!.body).toEqual({ simulation: 'REJECT' });
    expect(screen.getByRole('link', { name: 'Volver al carrito' })).toBeTruthy();
    expect(screen.getByRole('link', { name: /Carrito, 2 unidades/ })).toBeTruthy();
  });

  it('SLOW_APPROVE: 202 PAYMENT_PENDING, the order is read every 3 s until the worker settles it', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.stubEnv('VITE_PAYMENT_DEMO', 'true');
    api.on('POST', PAY, () => json(202, order({ status: 'PAYMENT_PENDING', payment_status: 'REQUESTED' })));
    renderAt(`/pedidos/${ORDER_ID}`);
    await screen.findByRole('heading', { name: 'Pedido 0B9A6C1E' });
    chooseDemo(/Aprobación lenta/);
    api.on('GET', DETAIL, () => json(200, order({ status: 'PAYMENT_PENDING', payment_status: 'REQUESTED' })));
    fireEvent.click(payButton());
    expect(await screen.findByText(/Estamos confirmando tu pago/)).toBeTruthy();
    expect(getPayment(7, ORDER_ID)).toMatchObject({ simulation: 'SLOW_APPROVE' }); // kept while unknown
    const before = reads();
    await tick(POLL_INTERVAL_MS);
    expect(reads()).toBe(before + 1);

    api.on('GET', DETAIL, () => json(200, order({ status: 'PAID', inventory_status: 'CONFIRMED', payment_status: 'APPROVED' })));
    await tick(POLL_INTERVAL_MS);
    expect(await screen.findByText('Pago aprobado. Tu carrito se vació.')).toBeTruthy();
    await waitFor(() => expect(getPayment(7, ORDER_ID)).toBeNull());
    expect(pays()).toHaveLength(1); // never paid again
    const settled = reads();
    await tick(POLL_INTERVAL_MS * 3);
    expect(reads()).toBe(settled); // polling stopped
  });

  it('reload with a pending payment: only reads the order, no new payment', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    savePayment({ userId: 7, orderId: ORDER_ID, key: 'saved-key', simulation: 'SLOW_APPROVE' });
    api.on('GET', DETAIL, () => json(200, order({ status: 'PAYMENT_PENDING', payment_status: 'REQUESTED' })));
    renderAt(`/pedidos/${ORDER_ID}`);
    expect(await screen.findByText(/El resultado del pago aún no se conoce/)).toBeTruthy();
    expect(screen.queryByRole('button', { name: /Pagar/ })).toBeNull();
    await tick(POLL_INTERVAL_MS * 2);
    expect(reads()).toBeGreaterThanOrEqual(3);
    expect(pays()).toHaveLength(0);
  });

  it('uncertain payment, order still PENDING_PAYMENT: retry repeats the SAME key and mode, even after a reload', async () => {
    vi.stubEnv('VITE_PAYMENT_DEMO', 'true');
    api.once('POST', PAY, () => Promise.reject(new TypeError('Failed to fetch')));
    renderAt(`/pedidos/${ORDER_ID}`);
    await screen.findByRole('heading', { name: 'Pedido 0B9A6C1E' });
    chooseDemo('Rechazar el pago');
    fireEvent.click(payButton());
    expect(await screen.findByText(/No sabemos si el pago llegó/)).toBeTruthy();
    const original = payKey(0);
    expect(getPayment(7, ORDER_ID)).toMatchObject({ key: original, simulation: 'REJECT' });

    cleanup(); // reload
    renderAt(`/pedidos/${ORDER_ID}`);
    await screen.findByText(/intento de pago sin confirmar/);
    expect((screen.getByRole('radio', { name: 'Rechazar el pago' }) as HTMLInputElement).checked).toBe(true);
    expect(screen.getByRole('radio', { name: 'Aprobar el pago' }).matches(':disabled')).toBe(true); // mode locked
    api.on('POST', PAY, () => json(200, order({ status: 'PAYMENT_REJECTED', inventory_status: 'RELEASED' })));
    fireEvent.click(screen.getByRole('button', { name: 'Reintentar pago' }));
    await screen.findByText(/El pago fue rechazado\. Tu carrito se conservó/);
    expect(payKey(1)).toBe(original);
    expect(pays()[1]!.body).toEqual({ simulation: 'REJECT' });
  });

  it('uncertain payment that did arrive (PAYMENT_PENDING): no retry is offered, the order is followed', async () => {
    api.once('POST', PAY, () => json(504, { detail: { code: 'UPSTREAM_TIMEOUT', message: 'x' } }));
    renderAt(`/pedidos/${ORDER_ID}`);
    await screen.findByRole('heading', { name: 'Pedido 0B9A6C1E' });
    api.on('GET', DETAIL, () => json(200, order({ status: 'PAYMENT_PENDING' })));
    fireEvent.click(payButton());
    expect(await screen.findByText('Tu pago se está procesando. El resultado aparecerá aquí.')).toBeTruthy();
    expect(screen.queryByRole('button', { name: /Pagar|Reintentar pago/ })).toBeNull();
  });

  it('IDEMPOTENCY_KEY_REUSED (paid from another tab) and ORDER_NOT_PAYABLE (expired)', async () => {
    api.once('POST', PAY, () => json(409, { detail: { code: 'IDEMPOTENCY_KEY_REUSED', message: 'x' } }));
    renderAt(`/pedidos/${ORDER_ID}`);
    await screen.findByRole('heading', { name: 'Pedido 0B9A6C1E' });
    fireEvent.click(payButton());
    expect(await screen.findByText(/ya se inició desde otra pestaña/)).toBeTruthy();
    expect(getPayment(7, ORDER_ID)).toBeNull();

    api.on('POST', PAY, () => json(409, { detail: { code: 'ORDER_NOT_PAYABLE', message: 'x', status: 'EXPIRED' } }));
    fireEvent.click(payButton());
    expect(await screen.findByText('El tiempo para pagar este pedido terminó.')).toBeTruthy();
  });

  it('when the time runs out the payment is disabled and the order is read again', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    api.on('GET', DETAIL, () => json(200, order({ expires_at: new Date(Date.now() + 3000).toISOString() })));
    renderAt(`/pedidos/${ORDER_ID}`);
    await screen.findByRole('heading', { name: 'Pedido 0B9A6C1E' });
    expect(payButton().disabled).toBe(false);
    const before = reads();
    api.on('GET', DETAIL, () => json(200, order({ status: 'CANCELLED', cancel_reason: 'EXPIRED', inventory_status: 'RESERVED', expires_at: null })));
    await tick(4000);
    expect(await screen.findByText(/Venció el tiempo para pagar\. La liberación de las unidades reservadas está pendiente/)).toBeTruthy();
    expect(reads()).toBeGreaterThan(before);
    expect(screen.queryByRole('button', { name: /Pagar/ })).toBeNull();
    expect(screen.getByText('Cancelado')).toBeTruthy(); // EXPIRED is a reason, not a state
  });

  it('PAID with inventory still RESERVED: "confirmation pending" until CONFIRMED', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    api.on('GET', DETAIL, () => json(200, order({ status: 'PAID', inventory_status: 'RESERVED' })));
    renderAt(`/pedidos/${ORDER_ID}`);
    expect(await screen.findByText(/Pago aprobado\. Falta confirmar las unidades/)).toBeTruthy();
    api.on('GET', DETAIL, () => json(200, order({ status: 'PAID', inventory_status: 'CONFIRMED' })));
    await tick(POLL_INTERVAL_MS);
    expect(await screen.findByText('Compra completada.')).toBeTruthy();
  });

  it('cancelling: the message does not claim a release that is still pending', async () => {
    api.on('POST', `/api/orders/${ORDER_ID}/cancel`, () =>
      json(200, order({ status: 'CANCELLED', cancel_reason: 'USER', inventory_status: 'RESERVED', expires_at: null })));
    renderAt(`/pedidos/${ORDER_ID}`);
    await screen.findByRole('heading', { name: 'Pedido 0B9A6C1E' });
    fireEvent.click(screen.getByRole('button', { name: 'Cancelar pedido' }));
    fireEvent.click(screen.getByRole('button', { name: 'Sí, cancelar' }));
    expect(await screen.findByText(/Pedido cancelado\. La liberación de las unidades reservadas está pendiente/)).toBeTruthy();
    expect(screen.queryByText(/se liberaron/)).toBeNull();
    expect(screen.queryByRole('button', { name: 'Cancelar pedido' })).toBeNull();
  });

  it('an admin sees another customer’s order read-only: no payment, no cancellation', async () => {
    signIn(api, admin);
    api.on('GET', DETAIL, () => json(200, order({ user_id: 7 })));
    renderAt(`/pedidos/${ORDER_ID}`);
    expect(await screen.findByText(/Pedido del cliente #7/)).toBeTruthy();
    expect(screen.queryByRole('button', { name: /Pagar/ })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Cancelar pedido' })).toBeNull();
    expect(screen.queryByText(/Pago simulado/)).toBeNull();
  });

  it('a saved payment of another user is never used or shown', async () => {
    savePayment({ userId: 1, orderId: ORDER_ID, key: 'admin-key', simulation: 'REJECT' });
    renderAt(`/pedidos/${ORDER_ID}`); // Ana (id 7), the owner
    await screen.findByRole('heading', { name: 'Pedido 0B9A6C1E' });
    expect(screen.queryByText(/intento de pago sin confirmar/)).toBeNull();
    api.on('POST', PAY, () => json(200, order({ status: 'PAID', inventory_status: 'CONFIRMED' })));
    fireEvent.click(payButton());
    await screen.findByText('Pago aprobado. Tu carrito se vació.');
    expect(payKey(0)).not.toBe('admin-key');
    expect(pays()[0]!.body).toEqual({ simulation: 'APPROVE' });
    expect(getPayment(1, ORDER_ID)?.key).toBe('admin-key');
  });

  it('an old reading that arrives after a newer answer (e.g. of POST /pay) does not bring back the old state', async () => {
    let tracker!: OrderTracker;
    function Probe() {
      tracker = useOrderTracker(ORDER_ID);
      return <output data-testid="state">{tracker.order?.status ?? '-'}</output>;
    }
    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>,
    );
    await waitFor(() => expect(screen.getByTestId('state').textContent).toBe('PENDING_PAYMENT'));
    const slowRead = deferred();
    api.on('GET', DETAIL, () => slowRead.promise);
    let reading!: Promise<unknown>;
    act(() => {
      reading = tracker.refresh(); // sent before the newer answer
    });
    act(() => tracker.update(order({ status: 'PAID', inventory_status: 'CONFIRMED' })));
    expect(screen.getByTestId('state').textContent).toBe('PAID');
    await act(async () => {
      slowRead.resolve(json(200, order({ status: 'PENDING_PAYMENT' })));
      await reading;
    });
    expect(screen.getByTestId('state').textContent).toBe('PAID');
  });

  it('after 2 minutes the automatic reading stops and offers to refresh; leaving the page stops it too', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    api.on('GET', DETAIL, () => json(200, order({ status: 'PAYMENT_PENDING' })));
    renderAt(`/pedidos/${ORDER_ID}`);
    await screen.findByText(/El resultado del pago aún no se conoce/);
    await tick(121_000);
    expect(await screen.findByRole('button', { name: 'Actualizar' })).toBeTruthy();
    const stopped = reads();
    await tick(30_000);
    expect(reads()).toBe(stopped);
    fireEvent.click(screen.getByRole('button', { name: 'Actualizar' }));
    await waitFor(() => expect(reads()).toBe(stopped + 1));

    cleanup(); // leaving the page
    expect(vi.getTimerCount()).toBe(0);
    const left = reads();
    await tick(30_000);
    expect(reads()).toBe(left);
  });
});

describe('orders list', () => {
  const page = (items: ReturnType<typeof order>[], total = items.length, n = 1) => json(200, { items, total, page: n, size: 10 });

  it('customer: "Mis pedidos" with state, total and a link to each order', async () => {
    api.on('GET', '/api/orders', () => page([order(), order({ id: OTHER_ORDER_ID, status: 'PAID', inventory_status: 'CONFIRMED' })]));
    renderAt('/pedidos');
    expect(await screen.findByRole('heading', { name: 'Mis pedidos' })).toBeTruthy();
    // The heading is shown while the orders load: wait for the rows themselves
    expect((await screen.findByRole('link', { name: 'Pedido 0B9A6C1E' })).getAttribute('href')).toBe(`/pedidos/${ORDER_ID}`);
    expect(screen.getByText('Pagado')).toBeTruthy();
    expect(screen.getAllByText('$1.519,48')).toHaveLength(2);
  });

  it('admin: every customer’s orders, labelled; pages of 10', async () => {
    signIn(api, admin);
    api.on('GET', '/api/orders', ({ url }) =>
      page([order({ user_id: 7 }), order({ id: OTHER_ORDER_ID, user_id: 1 })], 25, Number(url.searchParams.get('page'))));
    renderAt('/pedidos?pagina=2');
    expect(await screen.findByRole('heading', { name: 'Pedidos de todos los clientes' })).toBeTruthy();
    expect(await screen.findByText(/Cliente #7/)).toBeTruthy();
    expect(screen.getByText(/Tuyo/)).toBeTruthy();
    expect(screen.getByText('Página 2 de 3')).toBeTruthy();
    const call = api.callsTo('GET /api/orders').at(-1)!;
    expect([call.url.searchParams.get('page'), call.url.searchParams.get('size')]).toEqual(['2', '10']);
  });

  it('no orders yet', async () => {
    api.on('GET', '/api/orders', () => page([], 0));
    renderAt('/pedidos');
    expect(await screen.findByText('Todavía no hay pedidos')).toBeTruthy();
  });
});
