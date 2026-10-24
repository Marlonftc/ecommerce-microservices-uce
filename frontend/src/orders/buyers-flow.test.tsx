// @vitest-environment jsdom
// Buyer of each order for an admin (GET /api/users?ids=, one grouped request per page) and
// the cancellation text depending on who looks at the order.
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AppRoutes } from '../App';
import type { OrderOut } from '../api/types';
import { AuthProvider } from '../auth/AuthContext';
import { createFakeApi, deferred, hang, json, type FakeApi } from '../test/fakeApi';
import { ORDER_ID, OTHER_ORDER_ID, order } from '../test/orders';
import { admin, cart, customer, otherCustomer, signIn } from '../test/shop';

let api: FakeApi;
const THIRD_ORDER_ID = 'a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d';
const FOURTH_ORDER_ID = 'b2c3d4e5-f6a7-4b8c-9d0e-1f2a3b4c5d6e';

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <AuthProvider>
        <AppRoutes />
      </AuthProvider>
    </MemoryRouter>,
  );
}

const ordersPage = (items: OrderOut[], total = items.length, n = 1) => json(200, { items, total, page: n, size: 10 });
const userCalls = () => api.callsTo('GET /api/users');
const idsOf = (i: number) => userCalls()[i]!.url.searchParams.get('ids');

beforeEach(() => {
  api = createFakeApi();
  vi.stubGlobal('fetch', api.fetchMock);
  sessionStorage.clear();
  api.on('GET', '/api/cart', () => json(200, cart(1)));
  api.on('GET', '/api/notifications', () => json(200, { items: [], total: 0, page: 1, size: 1 }));
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('admin orders list: buyers', () => {
  it('one grouped request per page, unique ids without the admin, names and emails shown', async () => {
    signIn(api, admin);
    api.on('GET', '/api/orders', () =>
      ordersPage([
        order({ user_id: 8 }),
        order({ id: OTHER_ORDER_ID, user_id: 7 }),
        order({ id: THIRD_ORDER_ID, user_id: 8 }),
        order({ id: FOURTH_ORDER_ID, user_id: 1 }), // the admin's own order
      ]));
    api.on('GET', '/api/users', () => json(200, [customer, otherCustomer]));
    renderAt('/pedidos');
    expect(await screen.findByText('Ana Pérez')).toBeTruthy();
    expect(screen.getAllByText('Luis Mora')).toHaveLength(2);
    expect(screen.getByText(/ana@example\.com/)).toBeTruthy();
    expect(screen.getByText('Tuyo')).toBeTruthy();
    expect(userCalls()).toHaveLength(1);
    expect(idsOf(0)).toBe('7,8');
  });

  it('an unknown user falls back to "Cliente #ID"', async () => {
    signIn(api, admin);
    api.on('GET', '/api/orders', () => ordersPage([order({ user_id: 7 }), order({ id: OTHER_ORDER_ID, user_id: 99 })]));
    api.on('GET', '/api/users', () => json(200, [customer]));
    renderAt('/pedidos');
    expect(await screen.findByText('Ana Pérez')).toBeTruthy();
    expect(screen.getByText('Cliente #99')).toBeTruthy();
    expect(screen.queryByText(/No pudimos obtener los nombres/)).toBeNull();
  });

  it('user-service down: the orders are shown with "Cliente #ID" and a notice', async () => {
    signIn(api, admin);
    api.on('GET', '/api/orders', () => ordersPage([order({ user_id: 7 })]));
    api.on('GET', '/api/users', () => json(502, { detail: { code: 'UPSTREAM_UNAVAILABLE', message: 'down' } }));
    renderAt('/pedidos');
    expect(await screen.findByText(/No pudimos obtener los nombres de los clientes/)).toBeTruthy();
    expect(screen.getByText('Cliente #7')).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Pedido 0B9A6C1E' })).toBeTruthy();
  });

  it('a slow user-service never blocks the list', async () => {
    signIn(api, admin);
    api.on('GET', '/api/orders', () => ordersPage([order({ user_id: 7 })]));
    api.on('GET', '/api/users', hang);
    renderAt('/pedidos');
    expect(await screen.findByRole('link', { name: 'Pedido 0B9A6C1E' })).toBeTruthy();
    expect(screen.getByText('Cliente #7')).toBeTruthy();
  });

  it('a late answer for page 1 never replaces the names of page 2', async () => {
    signIn(api, admin);
    api.on('GET', '/api/orders', ({ url }) =>
      url.searchParams.get('page') === '2'
        ? ordersPage([order({ id: OTHER_ORDER_ID, user_id: 8 })], 20, 2)
        : ordersPage([order({ user_id: 7 })], 20, 1));
    const late = deferred();
    api.once('GET', '/api/users', () => late.promise);
    api.on('GET', '/api/users', () => json(200, [otherCustomer]));
    renderAt('/pedidos');
    await screen.findByRole('link', { name: 'Pedido 0B9A6C1E' });
    fireEvent.click(screen.getByRole('link', { name: 'Siguiente' }));
    expect(await screen.findByText('Luis Mora')).toBeTruthy();
    late.resolve(json(200, [{ ...customer, id: 8, full_name: 'Nombre equivocado' }]));
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.getByText('Luis Mora')).toBeTruthy();
    expect(screen.queryByText('Nombre equivocado')).toBeNull();
    expect([idsOf(0), idsOf(1)]).toEqual(['7', '8']);
  });

  it('the error notice of one page is not shown while the next page loads its names', async () => {
    signIn(api, admin);
    api.on('GET', '/api/orders', ({ url }) =>
      url.searchParams.get('page') === '2'
        ? ordersPage([order({ id: OTHER_ORDER_ID, user_id: 8 })], 20, 2)
        : ordersPage([order({ user_id: 7 })], 20, 1));
    api.once('GET', '/api/users', () => json(502, { detail: { code: 'UPSTREAM_UNAVAILABLE', message: 'down' } }));
    api.on('GET', '/api/users', hang);
    renderAt('/pedidos');
    expect(await screen.findByText(/No pudimos obtener los nombres/)).toBeTruthy();
    fireEvent.click(screen.getByRole('link', { name: 'Siguiente' }));
    expect(await screen.findByText('Cliente #8')).toBeTruthy();
    expect(screen.queryByText(/No pudimos obtener los nombres/)).toBeNull();
  });

  it('a customer never calls the users endpoint (list and detail)', async () => {
    signIn(api, customer);
    // Even if an order of someone else arrived, a customer must not ask user-service
    api.on('GET', '/api/orders', () => ordersPage([order(), order({ id: OTHER_ORDER_ID, user_id: 8 })]));
    api.on('GET', `/api/orders/${ORDER_ID}`, () => json(200, order({ status: 'CANCELLED', cancel_reason: 'USER', inventory_status: 'RELEASED' })));
    renderAt('/pedidos');
    fireEvent.click(await screen.findByRole('link', { name: 'Pedido 0B9A6C1E' }));
    expect(await screen.findByText('Lo cancelaste tú. Las unidades reservadas se liberaron.')).toBeTruthy();
    expect(userCalls()).toHaveLength(0);
  });

  it('a customer on an order that is not theirs never calls the users endpoint', async () => {
    signIn(api, customer);
    api.on('GET', `/api/orders/${ORDER_ID}`, () => json(200, order({ user_id: 8 })));
    renderAt(`/pedidos/${ORDER_ID}`);
    expect(await screen.findByText(/Pedido del cliente #8\./)).toBeTruthy();
    await new Promise((r) => setTimeout(r, 20));
    expect(userCalls()).toHaveLength(0);
  });
});

describe('order detail: buyer and cancellation text', () => {
  it('admin on another customer’s order: buyer name and email, "Lo canceló el cliente"', async () => {
    signIn(api, admin);
    api.on('GET', `/api/orders/${ORDER_ID}`, () =>
      json(200, order({ user_id: 7, status: 'CANCELLED', cancel_reason: 'USER', inventory_status: 'RELEASED' })));
    api.on('GET', '/api/users', () => json(200, [customer]));
    renderAt(`/pedidos/${ORDER_ID}`);
    expect(await screen.findByText('Lo canceló el cliente. Las unidades reservadas se liberaron.')).toBeTruthy();
    await waitFor(() => expect(screen.getByText('Ana Pérez')).toBeTruthy());
    expect(screen.getByText(/\(ana@example\.com\)/)).toBeTruthy();
    expect(screen.queryByText(/Lo cancelaste tú/)).toBeNull();
    expect(userCalls()).toHaveLength(1);
    expect(idsOf(0)).toBe('7');
  });

  it('admin on another customer’s order with user-service down: "Pedido del cliente #7"', async () => {
    signIn(api, admin);
    api.on('GET', `/api/orders/${ORDER_ID}`, () => json(200, order({ user_id: 7 })));
    api.on('GET', '/api/users', () => json(503, { detail: 'unavailable' }));
    renderAt(`/pedidos/${ORDER_ID}`);
    expect(await screen.findByText(/Pedido del cliente #7\./)).toBeTruthy();
    expect(screen.getByText('Las unidades están reservadas a la espera del pago del cliente.')).toBeTruthy();
    expect(screen.queryByRole('button', { name: /Pagar/ })).toBeNull();
  });

  it('admin on their own cancelled order: "Lo cancelaste tú" and no users request', async () => {
    signIn(api, admin);
    api.on('GET', `/api/orders/${ORDER_ID}`, () =>
      json(200, order({ user_id: 1, status: 'CANCELLED', cancel_reason: 'USER', inventory_status: 'RESERVED' })));
    renderAt(`/pedidos/${ORDER_ID}`);
    expect(await screen.findByText(/^Lo cancelaste tú\. La liberación de las unidades reservadas está pendiente/)).toBeTruthy();
    expect(screen.queryByText(/Como administrador puedes consultarlo/)).toBeNull();
    expect(userCalls()).toHaveLength(0);
  });
});
