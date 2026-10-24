// @vitest-environment jsdom
// Accounts for an admin: paging, own row, confirmations, D11 notices, backend refusals,
// unknown outcomes and losing the admin role while the page is open.
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AppRoutes } from '../../App';
import type { UserOut } from '../../api/types';
import { AuthProvider } from '../../auth/AuthContext';
import { createFakeApi, deferred, json, type Call, type FakeApi } from '../../test/fakeApi';
import { admin, cart, signIn } from '../../test/shop';

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

const account = (id: number, overrides: Partial<UserOut> = {}): UserOut => ({
  id, email: `user${id}@example.com`, full_name: `Usuario ${id}`, role: 'customer', is_active: true, created_at: '2026-10-01T10:00:00Z', ...overrides,
});
const ana = account(7, { full_name: 'Ana Pérez', email: 'ana@example.com' });
const luis = account(8, { full_name: 'Luis Mora', email: 'luis@example.com', role: 'admin' });
const listCalls = () => api.callsTo('GET /api/users').filter((c: Call) => c.url.searchParams.get('limit'));
const idsCalls = () => api.callsTo('GET /api/users').filter((c: Call) => c.url.searchParams.get('ids'));
const patches = (id: number) => api.callsTo(`PATCH /api/users/${id}`);
const rowOf = (name: string) => within(screen.getByRole('list', { name: 'Usuarios' })).getByText(name).closest('li') as HTMLElement;
const networkError = () => {
  throw new TypeError('Failed to fetch');
};

beforeEach(() => {
  api = createFakeApi();
  vi.stubGlobal('fetch', api.fetchMock);
  sessionStorage.clear();
  signIn(api, admin);
  api.on('GET', '/api/cart', () => json(200, cart(1)));
  api.on('GET', '/api/users', ({ url }) =>
    url.searchParams.get('ids') ? json(200, []) : json(200, url.searchParams.get('offset') === '0' ? [admin, ana, luis] : []));
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('accounts list', () => {
  it('pages of 20 by limit/offset; "Siguiente" only when the page is full', async () => {
    const full = Array.from({ length: 20 }, (_, i) => account(100 + i));
    api.on('GET', '/api/users', ({ url }) => json(200, url.searchParams.get('offset') === '20' ? [account(200)] : full));
    renderAt('/admin/usuarios');
    expect(await screen.findByText('Usuario 100')).toBeTruthy();
    expect([listCalls()[0]!.url.searchParams.get('limit'), listCalls()[0]!.url.searchParams.get('offset')]).toEqual(['20', '0']);
    fireEvent.click(screen.getByRole('link', { name: 'Siguiente' }));
    expect(await screen.findByText('Usuario 200')).toBeTruthy();
    expect(listCalls().at(-1)!.url.searchParams.get('offset')).toBe('20');
    expect(screen.queryByRole('link', { name: 'Siguiente' })).toBeNull();
    expect(screen.getByRole('link', { name: 'Anterior' })).toBeTruthy();
  });

  it('the admin\'s own row has no actions', async () => {
    renderAt('/admin/usuarios');
    await screen.findByText('Ana Pérez');
    const own = rowOf('Admin UCE');
    expect(within(own).getByText('Tu cuenta')).toBeTruthy();
    expect(within(own).queryAllByRole('button')).toHaveLength(0);
    expect(within(rowOf('Ana Pérez')).getAllByRole('button')).toHaveLength(2);
    // Fewer than 20 accounts on page 1: no paging
    expect(screen.queryByRole('navigation', { name: 'Paginación' })).toBeNull();
  });
});

describe('changes', () => {
  it('asks for a confirmation, sends only the changed field and explains D11', async () => {
    api.on('PATCH', '/api/users/7', ({ body }) => json(200, { ...ana, ...(body as object) }));
    api.on('PATCH', '/api/users/8', ({ body }) => json(200, { ...luis, ...(body as object) }));
    renderAt('/admin/usuarios');
    await screen.findByText('Ana Pérez');

    fireEvent.click(within(rowOf('Ana Pérez')).getByRole('button', { name: 'Desactivar' }));
    expect(screen.getByText(/su token sigue aceptado hasta que venza \(máximo 30 minutos\)/)).toBeTruthy();
    expect(patches(7)).toHaveLength(0);
    fireEvent.click(screen.getByRole('button', { name: 'Confirmar' }));
    expect(await screen.findByText(/Desactivar la cuenta: hecho\./)).toBeTruthy();
    expect(screen.queryByRole('group', { name: 'Confirmar cambio' })).toBeNull();
    expect(patches(7)[0]!.body).toEqual({ is_active: false });
    expect(within(rowOf('Ana Pérez')).getByText('Inactiva')).toBeTruthy();

    fireEvent.click(within(rowOf('Luis Mora')).getByRole('button', { name: 'Quitar rol de administrador' }));
    expect(screen.getByText(/conserva esos permisos hasta que su token venza/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Confirmar' }));
    await screen.findByText(/Quitar el rol de administrador: hecho\./);
    expect(patches(8)[0]!.body).toEqual({ role: 'customer' });

    fireEvent.click(within(rowOf('Ana Pérez')).getByRole('button', { name: 'Hacer administrador' }));
    expect(screen.getByText(/debe iniciar sesión de nuevo/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Cancelar' }));
    expect(patches(7)).toHaveLength(1);
  });

  it('CANNOT_MODIFY_SELF, LAST_ACTIVE_ADMIN and 404 from the backend', async () => {
    api.once('PATCH', '/api/users/8', () => json(409, { detail: { code: 'LAST_ACTIVE_ADMIN', message: 'x' } }));
    api.once('PATCH', '/api/users/8', () => json(409, { detail: { code: 'CANNOT_MODIFY_SELF', message: 'x' } }));
    api.once('PATCH', '/api/users/7', () => json(404, { detail: 'User not found' }));
    renderAt('/admin/usuarios');
    await screen.findByText('Ana Pérez');
    fireEvent.click(within(rowOf('Luis Mora')).getByRole('button', { name: 'Desactivar' }));
    fireEvent.click(screen.getByRole('button', { name: 'Confirmar' }));
    expect(await screen.findByText('No se aplicó: debe quedar al menos un administrador activo.')).toBeTruthy();
    fireEvent.click(within(rowOf('Luis Mora')).getByRole('button', { name: 'Desactivar' }));
    fireEvent.click(screen.getByRole('button', { name: 'Confirmar' }));
    expect(await screen.findByText('No puedes modificar tu propia cuenta.')).toBeTruthy();
    const before = listCalls().length;
    fireEvent.click(within(rowOf('Ana Pérez')).getByRole('button', { name: 'Desactivar' }));
    fireEvent.click(screen.getByRole('button', { name: 'Confirmar' }));
    await waitFor(() => expect(listCalls().length).toBe(before + 1));
  });

  it('unknown outcome, the account now holds the requested value: reported without claiming it was this request', async () => {
    api.once('PATCH', '/api/users/7', networkError);
    api.on('GET', '/api/users', ({ url }) =>
      url.searchParams.get('ids') ? json(200, [{ ...ana, is_active: false }]) : json(200, [admin, ana, luis]));
    renderAt('/admin/usuarios');
    await screen.findByText('Ana Pérez');
    fireEvent.click(within(rowOf('Ana Pérez')).getByRole('button', { name: 'Desactivar' }));
    fireEvent.click(screen.getByRole('button', { name: 'Confirmar' }));
    expect(await screen.findByText(/no sabemos si el cambio se aplicó/)).toBeTruthy();
    await new Promise((r) => setTimeout(r, 20));
    expect(patches(7)).toHaveLength(1); // never repeated by itself
    fireEvent.click(screen.getByRole('button', { name: 'Comprobar estado' }));
    expect(await screen.findByText(/Estado actual: inactiva, cliente\. Ya tiene el valor solicitado \(pudo aplicarlo esta solicitud u otro cambio simultáneo\)/)).toBeTruthy();
    expect(idsCalls()[0]!.url.searchParams.get('ids')).toBe('7');
    expect(patches(7)).toHaveLength(1);
  });

  it('unknown outcome followed by a concurrent change: the current state is shown as it is', async () => {
    api.once('PATCH', '/api/users/7', () => json(504, { detail: { code: 'UPSTREAM_TIMEOUT', message: 'x' } }));
    // Another admin promoted Ana and she is still active: the deactivation is not there
    api.on('GET', '/api/users', ({ url }) =>
      url.searchParams.get('ids') ? json(200, [{ ...ana, role: 'admin' }]) : json(200, [admin, ana, luis]));
    renderAt('/admin/usuarios');
    await screen.findByText('Ana Pérez');
    fireEvent.click(within(rowOf('Ana Pérez')).getByRole('button', { name: 'Desactivar' }));
    fireEvent.click(screen.getByRole('button', { name: 'Confirmar' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Comprobar estado' }));
    expect(await screen.findByText(/Estado actual: activa, administrador\. No tiene el valor solicitado; puedes volver a intentarlo\./)).toBeTruthy();
    expect(within(rowOf('Ana Pérez')).getByText('Administrador')).toBeTruthy();
    expect(patches(7)).toHaveLength(1);
  });

  it('a double click sends one PATCH', async () => {
    const slow = deferred();
    api.on('PATCH', '/api/users/7', () => slow.promise);
    renderAt('/admin/usuarios');
    await screen.findByText('Ana Pérez');
    fireEvent.click(within(rowOf('Ana Pérez')).getByRole('button', { name: 'Desactivar' }));
    const confirm = screen.getByRole('button', { name: 'Confirmar' });
    fireEvent.click(confirm);
    fireEvent.click(confirm);
    await act(async () => {
      slow.resolve(json(200, { ...ana, is_active: false }));
    });
    await screen.findByText(/Desactivar la cuenta: hecho\./);
    expect(patches(7)).toHaveLength(1);
  });
});

describe('losing the admin role while the page is open', () => {
  it('403 -> /users/me again; demoted: the session ends and no more admin requests are made', async () => {
    renderAt('/admin/usuarios');
    await screen.findByText('Ana Pérez');
    expect(api.callsTo('GET /api/users/me')).toHaveLength(1);
    // Another admin removed this admin's role: user-service answers 403 and /me says customer
    api.on('PATCH', '/api/users/7', () => json(403, { detail: 'Admin role required' }));
    api.on('GET', '/api/users/me', () => json(200, { ...admin, role: 'customer' }));
    fireEvent.click(within(rowOf('Ana Pérez')).getByRole('button', { name: 'Desactivar' }));
    fireEvent.click(screen.getByRole('button', { name: 'Confirmar' }));
    expect(await screen.findByText('Tus permisos cambiaron. Inicia sesión de nuevo para continuar.')).toBeTruthy();
    expect(api.callsTo('GET /api/users/me')).toHaveLength(2);
    const adminCalls = () => api.calls.filter((c) => /^(GET|PATCH|POST) \/api\/(users(\/\d+)?|admin\/operations|notifications)$/.test(c.key) && c.key !== 'GET /api/users/me').length;
    const before = adminCalls();
    await new Promise((r) => setTimeout(r, 50));
    expect(adminCalls()).toBe(before);
    expect(screen.queryByText('Ana Pérez')).toBeNull();
  });

  it('403 while still admin: the session is confirmed and the page loads again', async () => {
    api.once('GET', '/api/users', () => json(403, { detail: 'Admin role required' }));
    renderAt('/admin/usuarios');
    expect(await screen.findByText('Ana Pérez')).toBeTruthy();
    expect(api.callsTo('GET /api/users/me')).toHaveLength(2);
    expect(listCalls()).toHaveLength(2);
  });

  it('logout with an admin request still running: its late answer changes nothing', async () => {
    const slow = deferred();
    api.once('GET', '/api/users', () => slow.promise);
    renderAt('/admin/usuarios');
    await waitFor(() => expect(listCalls()).toHaveLength(1));
    fireEvent.click(screen.getByRole('button', { name: 'Cerrar sesión' }));
    await screen.findByRole('link', { name: 'Iniciar sesión' });
    await act(async () => {
      slow.resolve(json(200, [admin, ana]));
    });
    expect(screen.queryByText('Ana Pérez')).toBeNull();
    expect(listCalls()).toHaveLength(1);
  });
});
