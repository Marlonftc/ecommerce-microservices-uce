// @vitest-environment jsdom
// Role-aware links and post-login destinations; HTTP is isolated from the real platform.
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AppRoutes } from '../App';
import { AuthProvider } from '../auth/AuthContext';
import { SESSION_KEY } from '../auth/session';
import { createFakeApi, json, type FakeApi } from '../test/fakeApi';
import { admin, cart, customer, signIn } from '../test/shop';
import { makeToken } from '../test/tokens';

let api: FakeApi;

function Where() {
  const location = useLocation();
  return <output data-testid="where">{location.pathname}{location.search}</output>;
}

function renderAt(path: string, state?: unknown) {
  return render(
    <MemoryRouter initialEntries={[{ pathname: path, state }]}>
      <AuthProvider><AppRoutes /><Where /></AuthProvider>
    </MemoryRouter>,
  );
}

const primary = () => within(screen.getByRole('navigation', { name: 'Navegación principal' }));
const management = () => within(screen.getByRole('navigation', { name: 'Navegación administrativa' }));
const brand = () => screen.getByRole('link', { name: /E-Commerce UCE/ });

beforeEach(() => {
  sessionStorage.clear();
  api = createFakeApi();
  vi.stubGlobal('fetch', api.fetchMock);
  api.on('GET', '/api/cart', () => json(200, cart(1, [{ id: 1, quantity: 2 }])));
  api.on('GET', '/api/notifications', () => json(200, { items: [], total: 3, page: 1, size: 1 }));
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('navigation by verified role', () => {
  it('keeps public navigation and sends the logo to the commercial Home', () => {
    renderAt('/');
    expect(primary().getAllByRole('link').map((link) => link.getAttribute('href'))).toEqual([
      '/', '/productos', '/login', '/registro',
    ]);
    expect(brand().getAttribute('href')).toBe('/');
    expect(screen.queryByRole('navigation', { name: 'Navegación administrativa' })).toBeNull();
  });

  it('keeps all customer links, cart units and the personal notification counter', async () => {
    signIn(api, customer);
    renderAt('/cuenta');
    await screen.findByRole('heading', { name: 'Mi cuenta' });
    await waitFor(() => expect(primary().getByRole('link', { name: 'Carrito, 2 unidades' })).toBeTruthy());
    expect(await primary().findByRole('link', { name: 'Notificaciones, 3 sin leer' })).toBeTruthy();
    expect(primary().getAllByRole('link').map((link) => link.getAttribute('href'))).toEqual([
      '/', '/productos', '/carrito', '/pedidos', '/notificaciones', '/cuenta',
    ]);
    expect(primary().getByRole('button', { name: 'Cerrar sesión' })).toBeTruthy();
    expect(brand().getAttribute('href')).toBe('/');
    expect(screen.queryByRole('navigation', { name: 'Navegación administrativa' })).toBeNull();
    fireEvent.click(brand());
    expect(await screen.findByRole('heading', { level: 1, name: 'Tecnología para lo que viene.' })).toBeTruthy();
  });

  it('shows every management destination and a compatible account view without shopping links or a personal bell', async () => {
    signIn(api, admin);
    renderAt('/cuenta');
    expect(await screen.findByText('Administrador')).toBeTruthy();
    expect(primary().getAllByRole('link').map((link) => link.getAttribute('href'))).toEqual(['/admin', '/cuenta']);
    expect(management().getAllByRole('link').map((link) => link.getAttribute('href'))).toEqual([
      '/admin/productos', '/pedidos', '/admin/usuarios', '/admin/operaciones', '/admin/notificaciones',
    ]);
    expect(primary().queryByRole('link', { name: 'Catálogo' })).toBeNull();
    expect(primary().queryByRole('link', { name: /^Carrito/ })).toBeNull();
    expect(primary().queryByRole('link', { name: /^Notificaciones/ })).toBeNull();
    expect(api.callsTo('GET /api/notifications')).toHaveLength(0);
    expect(brand().getAttribute('href')).toBe('/admin');
    fireEvent.click(brand());
    expect(await screen.findByRole('heading', { name: 'Administración' })).toBeTruthy();
    expect(primary().getByRole('link', { name: 'Panel principal' }).getAttribute('aria-current')).toBe('page');
  });

  it.each([
    ['Productos e inventario', '/admin/productos', 'Productos'],
    ['Pedidos', '/pedidos', 'Pedidos de todos los clientes'],
    ['Usuarios', '/admin/usuarios', 'Usuarios'],
    ['Operaciones pendientes de la Saga', '/admin/operaciones', 'Operaciones pendientes'],
    ['Notificaciones administrativas', '/admin/notificaciones', 'Notificaciones de clientes'],
  ])('opens the existing %s page from the management navigation', async (label, path, heading) => {
    signIn(api, admin);
    // Empty list fixtures keep this test focused on the route and its authorization.
    api.on('GET', '/api/products', () => json(200, { items: [], total: 0, limit: 20, offset: 0 }));
    api.on('GET', '/api/orders', () => json(200, { items: [], total: 0, page: 1, size: 20 }));
    api.on('GET', '/api/users', () => json(200, { items: [], total: 0, limit: 20, offset: 0 }));
    api.on('GET', '/api/admin/operations', () => json(200, { items: [], total: 0, page: 1, size: 20 }));
    api.on('GET', '/api/notifications', () => json(200, { items: [], total: 0, page: 1, size: 20 }));
    renderAt('/admin');
    await screen.findByRole('heading', { name: 'Administración' });
    fireEvent.click(management().getByRole('link', { name: label }));
    await waitFor(() => expect(screen.getByTestId('where').textContent).toBe(path));
    expect(await screen.findByRole('heading', { level: 1, name: heading })).toBeTruthy();
    expect(management().getByRole('link', { name: label }).getAttribute('aria-current')).toBe('page');
    expect(primary().getByRole('link', { name: 'Panel principal' }).getAttribute('aria-current')).toBeNull();
    expect(api.calls.every((call) => call.key.startsWith('GET '))).toBe(true);
  });

  it.each([undefined, { from: '/carrito' }, { from: '//external.example' }])('lands an admin login in the panel (return state: %j)', async (state) => {
    const token = makeToken({ sub: '1', role: 'admin' });
    api.on('POST', '/api/auth/login', () => json(200, { access_token: token, token_type: 'bearer', expires_in: 1800 }));
    api.on('GET', '/api/users/me', () => json(200, admin));
    renderAt('/login', state);
    fireEvent.change(screen.getByLabelText('Correo'), { target: { value: 'admin@example.com' } });
    fireEvent.change(screen.getByLabelText('Contraseña'), { target: { value: 'test-only' } });
    fireEvent.click(screen.getByRole('button', { name: 'Iniciar sesión' }));
    expect(await screen.findByRole('heading', { name: 'Administración' })).toBeTruthy();
    expect(screen.getByTestId('where').textContent).toBe('/admin');
    expect(api.callsTo('POST /api/auth/login')).toHaveLength(1);
  });

  it('clears management navigation on logout and restores public navigation', async () => {
    signIn(api, admin);
    renderAt('/admin');
    await screen.findByRole('heading', { name: 'Administración' });
    fireEvent.click(primary().getByRole('button', { name: 'Cerrar sesión' }));
    expect(await primary().findByRole('link', { name: 'Iniciar sesión' })).toBeTruthy();
    expect(screen.queryByRole('navigation', { name: 'Navegación administrativa' })).toBeNull();
    expect(brand().getAttribute('href')).toBe('/');
    expect(sessionStorage.getItem(SESSION_KEY)).toBeNull();
  });
});
