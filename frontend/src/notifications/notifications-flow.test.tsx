// @vitest-environment jsdom
// Notifications of a customer: the bell counter (total of unread_only=true), its refresh
// rules (pages, after a change, every 60 s while visible, on visibility, per session) and
// the /notificaciones page.
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AppRoutes } from '../App';
import type { NotificationOut } from '../api/types';
import { AuthProvider } from '../auth/AuthContext';
import { createFakeApi, deferred, json, type Call, type FakeApi } from '../test/fakeApi';
import { ORDER_ID, order } from '../test/orders';
import { admin, cart, customer, otherCustomer, signIn } from '../test/shop';
import { makeToken } from '../test/tokens';
import { UNREAD_POLL_MS } from './NotificationsContext';

let api: FakeApi;
let visibility: DocumentVisibilityState = 'visible';

const N1 = '11111111-1111-4111-8111-111111111111';
const N2 = '22222222-2222-4222-8222-222222222222';

function notification(id: string, overrides: Partial<NotificationOut> = {}): NotificationOut {
  return {
    id,
    event_id: `${ORDER_ID}:ORDER_PAID`,
    user_id: 7,
    order_id: ORDER_ID,
    type: 'ORDER_PAID',
    amount: '1519.48',
    currency: 'USD',
    title: 'Pago aprobado',
    message: 'Tu pedido fue pagado por 1519.48 USD.',
    is_read: false,
    read_at: null,
    created_at: '2026-10-22T20:05:00Z',
    ...overrides,
  };
}

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <AuthProvider>
        <AppRoutes />
      </AuthProvider>
    </MemoryRouter>,
  );
}

const isCount = (call: Call) => call.url.searchParams.get('unread_only') === 'true' && call.url.searchParams.get('size') === '1';
const countCalls = () => api.callsTo('GET /api/notifications').filter(isCount).length;
const listCalls = () => api.callsTo('GET /api/notifications').filter((c) => !isCount(c));
const bell = () => screen.getByRole('link', { name: /^Notificaciones/ });
const tick = (ms: number) => act(async () => {
  await vi.advanceTimersByTimeAsync(ms);
});

/** Unread counter answered from a variable, list pages from `items` */
let unread = 3;
let items: NotificationOut[] = [];
function serveNotifications() {
  api.on('GET', '/api/notifications', ({ url }) => {
    if (url.searchParams.get('size') === '1') return json(200, { items: [], total: unread, page: 1, size: 1 });
    return json(200, { items, total: items.length, page: Number(url.searchParams.get('page')), size: 20 });
  });
}

function setVisibility(state: DocumentVisibilityState) {
  visibility = state;
  document.dispatchEvent(new Event('visibilitychange'));
}

beforeEach(() => {
  api = createFakeApi();
  vi.stubGlobal('fetch', api.fetchMock);
  sessionStorage.clear();
  visibility = 'visible';
  Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => visibility });
  unread = 3;
  items = [notification(N1), notification(N2, { title: 'Pedido cancelado', is_read: true, read_at: '2026-10-22T21:00:00Z' })];
  api.on('GET', '/api/cart', () => json(200, cart(1)));
  serveNotifications();
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('bell counter', () => {
  it('shows the total of unread notifications from the backend (not the items received)', async () => {
    signIn(api, customer);
    renderAt('/');
    await waitFor(() => expect(bell().getAttribute('aria-label')).toBe('Notificaciones, 3 sin leer'));
    expect(bell().textContent).toContain('3');
    const call = api.callsTo('GET /api/notifications')[0]!;
    expect([call.url.searchParams.get('unread_only'), call.url.searchParams.get('size')]).toEqual(['true', '1']);
  });

  it('zero unread: no badge', async () => {
    unread = 0;
    signIn(api, customer);
    renderAt('/');
    await waitFor(() => expect(bell().getAttribute('aria-label')).toBe('Notificaciones, 0 sin leer'));
    expect(bell().textContent).toBe('');
  });

  it('a failed read is never shown as 0', async () => {
    api.on('GET', '/api/notifications', () => json(502, { detail: { code: 'UPSTREAM_UNAVAILABLE', message: 'down' } }));
    signIn(api, customer);
    renderAt('/');
    await waitFor(() => expect(bell().getAttribute('aria-label')).toMatch(/no se pudo consultar/));
    expect(bell().textContent).toBe('?');
    expect(bell().getAttribute('aria-label')).not.toMatch(/0 sin leer/);
  });

  it('an admin has no bell and the app never asks for notifications', async () => {
    signIn(api, admin);
    renderAt('/');
    await screen.findByRole('link', { name: 'Administración' });
    expect(screen.queryByRole('link', { name: /^Notificaciones/ })).toBeNull();
    await new Promise((r) => setTimeout(r, 20));
    expect(api.callsTo('GET /api/notifications')).toHaveLength(0);
  });

  it('an admin opening /notificaciones gets the forbidden page, without requests', async () => {
    signIn(api, admin);
    renderAt('/notificaciones');
    expect(await screen.findByRole('heading', { name: /Acceso denegado|No tienes permiso/ })).toBeTruthy();
    expect(api.callsTo('GET /api/notifications')).toHaveLength(0);
  });

  it('polls every 60 s only while the tab is visible, and refreshes when it becomes visible', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    signIn(api, customer);
    renderAt('/');
    await waitFor(() => expect(countCalls()).toBe(1));
    await tick(UNREAD_POLL_MS);
    expect(countCalls()).toBe(2);

    setVisibility('hidden');
    await tick(UNREAD_POLL_MS * 3);
    expect(countCalls()).toBe(2);

    unread = 5;
    setVisibility('visible');
    await waitFor(() => expect(countCalls()).toBe(3));
    await waitFor(() => expect(bell().getAttribute('aria-label')).toBe('Notificaciones, 5 sin leer'));
  });

  it('never runs two counter reads at the same time', async () => {
    const slow = deferred();
    api.once('GET', '/api/notifications', () => slow.promise);
    signIn(api, customer);
    renderAt('/');
    await waitFor(() => expect(countCalls()).toBe(1));
    setVisibility('visible');
    setVisibility('visible');
    fireEvent.click(screen.getByRole('link', { name: /^Notificaciones/ })); // visiting the page also refreshes
    await screen.findByRole('heading', { name: 'Notificaciones' });
    expect(countCalls()).toBe(1);
    slow.resolve(json(200, { items: [], total: 3, page: 1, size: 1 }));
    await waitFor(() => expect(bell().getAttribute('aria-label')).toBe('Notificaciones, 3 sin leer'));
  });

  it('logout stops the timers and a late answer of the old session is ignored', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const setSpy = vi.spyOn(globalThis, 'setInterval');
    const clearSpy = vi.spyOn(globalThis, 'clearInterval');
    const late = deferred();
    api.once('GET', '/api/notifications', () => late.promise);
    signIn(api, customer);
    renderAt('/');
    await waitFor(() => expect(countCalls()).toBe(1));
    const pollTimer = setSpy.mock.results[setSpy.mock.calls.findIndex((c) => c[1] === UNREAD_POLL_MS)]!.value;

    fireEvent.click(screen.getByRole('button', { name: 'Cerrar sesión' }));
    await screen.findByRole('link', { name: 'Iniciar sesión' });
    expect(clearSpy).toHaveBeenCalledWith(pollTimer);
    await tick(UNREAD_POLL_MS * 2);
    expect(countCalls()).toBe(1);

    // Another customer signs in; the first session's answer arrives afterwards
    unread = 1;
    const token = makeToken({ sub: '8', role: 'customer' }, 'sig-8');
    api.on('POST', '/api/auth/login', () => json(200, { access_token: token, token_type: 'bearer', expires_in: 1800 }));
    api.on('GET', '/api/users/me', () => json(200, otherCustomer));
    fireEvent.click(screen.getByRole('link', { name: 'Iniciar sesión' }));
    fireEvent.change(await screen.findByLabelText('Correo'), { target: { value: 'luis@example.com' } });
    fireEvent.change(screen.getByLabelText('Contraseña'), { target: { value: 'Clave1234' } });
    fireEvent.click(screen.getByRole('button', { name: 'Iniciar sesión' }));
    await waitFor(() => expect(bell().getAttribute('aria-label')).toBe('Notificaciones, 1 sin leer'));

    late.resolve(json(200, { items: [], total: 9, page: 1, size: 1 }));
    await tick(50);
    expect(bell().getAttribute('aria-label')).toBe('Notificaciones, 1 sin leer');
  });

  it('a change during a running read triggers one more read when it ends', async () => {
    const slow = deferred();
    signIn(api, customer);
    api.on('PATCH', `/api/notifications/${N1}/read`, () => json(200, notification(N1, { is_read: true, read_at: '2026-10-22T22:00:00Z' })));
    renderAt('/notificaciones');
    await waitFor(() => expect(bell().getAttribute('aria-label')).toBe('Notificaciones, 3 sin leer'));
    // The next counter read is slow; the notification is marked while it runs
    api.once('GET', '/api/notifications', () => slow.promise);
    setVisibility('visible');
    await waitFor(() => expect(countCalls()).toBe(2));
    fireEvent.click(await screen.findByRole('button', { name: 'Marcar como leída' }));
    await waitFor(() => expect(screen.queryByText('Nueva')).toBeNull());
    expect(countCalls()).toBe(2); // joined, not a parallel request
    unread = 2;
    slow.resolve(json(200, { items: [], total: 3, page: 1, size: 1 })); // read started before the change
    await waitFor(() => expect(countCalls()).toBe(3));
    await waitFor(() => expect(bell().getAttribute('aria-label')).toBe('Notificaciones, 2 sin leer'));
  });

  it('opening the orders page refreshes the counter', async () => {
    signIn(api, customer);
    api.on('GET', '/api/orders', () => json(200, { items: [], total: 0, page: 1, size: 10 }));
    renderAt('/');
    await waitFor(() => expect(bell().getAttribute('aria-label')).toBe('Notificaciones, 3 sin leer'));
    expect(countCalls()).toBe(1);
    unread = 6;
    fireEvent.click(screen.getByRole('link', { name: 'Mis pedidos' }));
    await waitFor(() => expect(countCalls()).toBe(2));
    await waitFor(() => expect(bell().getAttribute('aria-label')).toBe('Notificaciones, 6 sin leer'));
  });

  it('refreshes after an order of the customer is paid', async () => {
    signIn(api, customer);
    api.on('GET', `/api/orders/${ORDER_ID}`, () => json(200, order()));
    api.on('POST', `/api/orders/${ORDER_ID}/pay`, () => {
      unread = 4;
      return json(200, order({ status: 'PAID', inventory_status: 'CONFIRMED' }));
    });
    renderAt(`/pedidos/${ORDER_ID}`);
    fireEvent.click(await screen.findByRole('button', { name: /^Pagar/ }));
    await screen.findByText('Pago aprobado. Tu carrito se vació.');
    await waitFor(() => expect(bell().getAttribute('aria-label')).toBe('Notificaciones, 4 sin leer'));
  });
});

describe('notifications page', () => {
  it('lists the notifications, 20 per page, with a link to the order', async () => {
    signIn(api, customer);
    renderAt('/notificaciones');
    expect(await screen.findByText('Pago aprobado')).toBeTruthy();
    expect(screen.getByText('Pedido cancelado')).toBeTruthy();
    expect(screen.getAllByText('Nueva')).toHaveLength(1);
    const call = listCalls()[0]!;
    expect([call.url.searchParams.get('page'), call.url.searchParams.get('size'), call.url.searchParams.get('unread_only')])
      .toEqual(['1', '20', null]);
  });

  it('opens the related order from a notification', async () => {
    signIn(api, customer);
    api.on('GET', `/api/orders/${ORDER_ID}`, () => json(200, order({ status: 'PAID', inventory_status: 'CONFIRMED' })));
    renderAt('/notificaciones');
    fireEvent.click((await screen.findAllByRole('link', { name: 'Ver pedido' }))[0]!);
    expect(await screen.findByRole('heading', { name: 'Pedido 0B9A6C1E' })).toBeTruthy();
  });

  it('marks a notification as read once (double click) and refreshes the counter at once', async () => {
    signIn(api, customer);
    api.on('PATCH', `/api/notifications/${N1}/read`, () => {
      unread = 2;
      return json(200, notification(N1, { is_read: true, read_at: '2026-10-22T22:00:00Z' }));
    });
    renderAt('/notificaciones');
    await waitFor(() => expect(bell().getAttribute('aria-label')).toBe('Notificaciones, 3 sin leer'));
    const button = await screen.findByRole('button', { name: 'Marcar como leída' });
    fireEvent.click(button);
    fireEvent.click(button);
    await waitFor(() => expect(screen.queryByText('Nueva')).toBeNull());
    expect(api.callsTo(`PATCH /api/notifications/${N1}/read`)).toHaveLength(1);
    await waitFor(() => expect(bell().getAttribute('aria-label')).toBe('Notificaciones, 2 sin leer'));
  });

  it('a failed mark is shown on the notification and nothing changes', async () => {
    signIn(api, customer);
    api.on('PATCH', `/api/notifications/${N1}/read`, () => json(503, { detail: 'unavailable' }));
    renderAt('/notificaciones');
    fireEvent.click(await screen.findByRole('button', { name: 'Marcar como leída' }));
    expect(await screen.findByText('No se pudo marcar como leída.')).toBeTruthy();
    expect(screen.getByText('Nueva')).toBeTruthy();
  });

  it('the unread filter and the page number go to the request', async () => {
    signIn(api, customer);
    items = Array.from({ length: 20 }, (_, i) => notification(`${String(i).padStart(8, '0')}-1111-4111-8111-111111111111`));
    api.on('GET', '/api/notifications', ({ url }) =>
      url.searchParams.get('size') === '1'
        ? json(200, { items: [], total: 3, page: 1, size: 1 })
        : json(200, { items, total: 45, page: Number(url.searchParams.get('page')), size: 20 }));
    renderAt('/notificaciones');
    await screen.findByText('Página 1 de 3');
    fireEvent.click(screen.getByRole('checkbox', { name: 'Solo no leídas' }));
    await waitFor(() => expect(listCalls().at(-1)!.url.searchParams.get('unread_only')).toBe('true'));
    fireEvent.click(await screen.findByRole('link', { name: 'Siguiente' }));
    await waitFor(() => expect(listCalls().at(-1)!.url.searchParams.get('page')).toBe('2'));
    expect(listCalls().at(-1)!.url.searchParams.get('unread_only')).toBe('true');
  });

  it('empty states', async () => {
    items = [];
    signIn(api, customer);
    renderAt('/notificaciones');
    expect(await screen.findByText('Todavía no tienes notificaciones')).toBeTruthy();
    fireEvent.click(screen.getByRole('checkbox', { name: 'Solo no leídas' }));
    expect(await screen.findByText('No tienes notificaciones sin leer')).toBeTruthy();
  });

  it('error with retry', async () => {
    signIn(api, customer);
    let listFailures = 1;
    api.on('GET', '/api/notifications', ({ url }) => {
      if (url.searchParams.get('size') === '1') return json(200, { items: [], total: 3, page: 1, size: 1 });
      if (listFailures-- > 0) return json(503, { detail: 'unavailable' });
      return json(200, { items, total: items.length, page: 1, size: 20 });
    });
    renderAt('/notificaciones');
    expect(await screen.findByText('No pudimos cargar las notificaciones.')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Reintentar' }));
    expect(await screen.findByText('Pago aprobado')).toBeTruthy();
  });

  it('titles and messages are text: HTML from the server is never interpreted', async () => {
    items = [notification(N1, { title: '<img src=x onerror="alert(1)">', message: '<b>negrita</b><script>alert(2)</script>' })];
    signIn(api, customer);
    const { container } = renderAt('/notificaciones');
    expect(await screen.findByText('<img src=x onerror="alert(1)">')).toBeTruthy();
    expect(screen.getByText('<b>negrita</b><script>alert(2)</script>')).toBeTruthy();
    expect(container.querySelector('.notification img, .notification b, .notification script')).toBeNull();
  });
});
