// @vitest-environment jsdom
// Customers' notifications for an admin (read-only) and access of a customer to the admin pages.
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AppRoutes } from '../../App';
import type { NotificationOut } from '../../api/types';
import { AuthProvider } from '../../auth/AuthContext';
import { createFakeApi, json, type Call, type FakeApi } from '../../test/fakeApi';
import { ORDER_ID } from '../../test/orders';
import { admin, cart, customer, otherCustomer, signIn } from '../../test/shop';

let api: FakeApi;

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <AuthProvider>
        <AppRoutes />
      </AuthProvider>
    </MemoryRouter>,
  );
}

const uuid = (n: number) => `${String(n).padStart(8, '0')}-1111-4111-8111-111111111111`;
function notification(n: number, overrides: Partial<NotificationOut> = {}): NotificationOut {
  return {
    id: uuid(n), event_id: `${ORDER_ID}:ORDER_PAID`, user_id: 7, order_id: ORDER_ID, type: 'ORDER_PAID', amount: '10.00', currency: 'USD',
    title: 'Pago confirmado', message: 'Tu pedido fue pagado.', is_read: false, read_at: null, created_at: '2026-10-23T09:00:00Z', ...overrides,
  };
}
const items = [
  notification(1, { user_id: 7 }),
  notification(2, { user_id: 8, is_read: true, read_at: '2026-10-23T09:30:00Z', title: 'Pedido cancelado' }),
  notification(3, { user_id: 7, title: 'Pago rechazado' }),
  notification(4, { user_id: 1, title: 'Pago del admin' }),
];
const listCalls = () => api.callsTo('GET /api/notifications');
const idsCalls = () => api.callsTo('GET /api/users').filter((c: Call) => c.url.searchParams.get('ids'));

beforeEach(() => {
  api = createFakeApi();
  vi.stubGlobal('fetch', api.fetchMock);
  sessionStorage.clear();
  signIn(api, admin);
  api.on('GET', '/api/cart', () => json(200, cart(1)));
  api.on('GET', '/api/notifications', ({ url }) => json(200, { items, total: 4, page: Number(url.searchParams.get('page')), size: 20 }));
  api.on('GET', '/api/users', () => json(200, [customer, otherCustomer]));
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('customers\' notifications (admin, read-only)', () => {
  it('every customer, with names from ONE grouped request without duplicates or the admin', async () => {
    renderAt('/admin/notificaciones');
    expect(await screen.findByText('Pedido cancelado')).toBeTruthy();
    await waitFor(() => expect(screen.getAllByText(/Ana Pérez/)).toHaveLength(2));
    expect(screen.getByText(/Luis Mora/)).toBeTruthy();
    expect(screen.getByText(/Tu cuenta/)).toBeTruthy();
    expect(idsCalls()).toHaveLength(1);
    expect(idsCalls()[0]!.url.searchParams.get('ids')).toBe('7,8');
    expect([listCalls()[0]!.url.searchParams.get('page'), listCalls()[0]!.url.searchParams.get('size')]).toEqual(['1', '20']);
  });

  it('read state belongs to the customer; there is no way to mark and no PATCH', async () => {
    renderAt('/admin/notificaciones');
    await screen.findByText('Pedido cancelado');
    expect(screen.getByText(/Leída por el cliente el/)).toBeTruthy();
    expect(screen.getAllByText('No leída por el cliente')).toHaveLength(3);
    expect(screen.queryByRole('button', { name: /Marcar/ })).toBeNull();
    expect(api.calls.some((c) => c.key.startsWith('PATCH'))).toBe(false);
  });

  it('user-service down: "Cliente #ID" and the list is still shown', async () => {
    api.on('GET', '/api/users', () => json(502, { detail: { code: 'UPSTREAM_UNAVAILABLE', message: 'x' } }));
    renderAt('/admin/notificaciones');
    expect(await screen.findByText(/No pudimos obtener los nombres de los clientes/)).toBeTruthy();
    expect(screen.getAllByText(/Cliente #7/)).toHaveLength(2);
    expect(screen.getByText('Pago rechazado')).toBeTruthy();
  });

  it('the "unread by the customer" filter goes to the request', async () => {
    renderAt('/admin/notificaciones');
    await screen.findByText('Pedido cancelado');
    fireEvent.click(screen.getByRole('checkbox', { name: 'Solo las no leídas por el cliente' }));
    await waitFor(() => expect(listCalls().at(-1)!.url.searchParams.get('unread_only')).toBe('true'));
  });

  it('titles and messages are text, never HTML', async () => {
    api.on('GET', '/api/notifications', () =>
      json(200, { items: [notification(1, { title: '<img src=x onerror=alert(1)>', message: '<b>x</b>' })], total: 1, page: 1, size: 20 }));
    const { container } = renderAt('/admin/notificaciones');
    expect(await screen.findByText('<img src=x onerror=alert(1)>')).toBeTruthy();
    expect(container.querySelector('.notification img, .notification b')).toBeNull();
  });

  it('the personal bell is not shown to the admin', async () => {
    renderAt('/admin/notificaciones');
    await screen.findByText('Pedido cancelado');
    expect(within(screen.getByRole('navigation', { name: 'Navegación principal' })).queryByRole('link', { name: /^Notificaciones/ })).toBeNull();
    expect(listCalls().every((c) => c.url.searchParams.get('size') === '20')).toBe(true);
  });
});

describe('a customer and the admin pages', () => {
  it('gets the forbidden page on the three pages, and no admin request is made', async () => {
    signIn(api, customer);
    api.on('GET', '/api/notifications', () => json(200, { items: [], total: 0, page: 1, size: 1 }));
    for (const path of ['/admin/usuarios', '/admin/operaciones', '/admin/notificaciones']) {
      const view = renderAt(path);
      expect(await within(view.container).findByRole('heading', { name: 'No tienes permiso para ver esta página' })).toBeTruthy();
      view.unmount();
    }
    expect(api.callsTo('GET /api/admin/operations')).toHaveLength(0);
    expect(api.callsTo('GET /api/users')).toHaveLength(0);
    expect(listCalls().every((c) => c.url.searchParams.get('size') === '1' && c.url.searchParams.get('unread_only') === 'true')).toBe(true);
  });
});
