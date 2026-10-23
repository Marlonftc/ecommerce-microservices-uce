// @vitest-environment jsdom
// Pending operations of the saga for an admin: filters, retry rules decided by order-service,
// locks, outcomes, unknown outcomes and the safe display of diagnosis data.
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AppRoutes } from '../../App';
import type { OperationOut, RetryOut } from '../../api/types';
import { AuthProvider } from '../../auth/AuthContext';
import { createFakeApi, deferred, json, type FakeApi } from '../../test/fakeApi';
import { ORDER_ID } from '../../test/orders';
import { admin, cart, signIn } from '../../test/shop';
import { OUTCOME_TEXT } from './AdminOperationsPage';

let api: FakeApi;
const LIST = 'GET /api/admin/operations';

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <AuthProvider>
        <AppRoutes />
      </AuthProvider>
    </MemoryRouter>,
  );
}

function op(id: number, overrides: Partial<OperationOut> = {}): OperationOut {
  return {
    id,
    order_id: ORDER_ID,
    kind: 'SEND_NOTIFICATION',
    operation_key: `${ORDER_ID}:SEND_NOTIFICATION:ORDER_PAID`,
    status: 'FAILED',
    attempts: 3,
    error_kind: 'TRANSIENT',
    last_error: 'notification-service unavailable',
    next_attempt_at: '2026-10-23T10:00:00Z',
    locked_until: null,
    payload: { event_type: 'ORDER_PAID', user_id: 7, amount: '10.00', currency: 'USD' },
    created_at: '2026-10-23T09:00:00Z',
    updated_at: '2026-10-23T09:30:00Z',
    completed_at: null,
    ...overrides,
  };
}
const page = (items: OperationOut[], total = items.length) => json(200, { items, total, page: 1, size: 20 });
const retries = (id: number) => api.callsTo(`POST /api/admin/operations/${id}/retry`);
const rowOf = (id: number) => screen.getByText(new RegExp(`^#${id} ·`)).closest('li') as HTMLElement;
const networkError = () => {
  throw new TypeError('Failed to fetch');
};

beforeEach(() => {
  api = createFakeApi();
  vi.stubGlobal('fetch', api.fetchMock);
  sessionStorage.clear();
  signIn(api, admin);
  api.on('GET', '/api/cart', () => json(200, cart(1)));
  api.on('GET', '/api/admin/operations', () => page([op(1)]));
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('list and filters', () => {
  it('filters from the URL go to the request; the total pages the list', async () => {
    api.on('GET', '/api/admin/operations', ({ url }) => json(200, { items: [op(1)], total: 45, page: Number(url.searchParams.get('page')), size: 20 }));
    renderAt(`/admin/operaciones?estado=FAILED&tipo=SEND_NOTIFICATION&error=PERMANENT&pedido=${ORDER_ID}&pagina=2`);
    await screen.findByText(/^#1 ·/);
    const call = api.callsTo(LIST)[0]!;
    expect(Object.fromEntries(call.url.searchParams)).toEqual({
      status: 'FAILED', kind: 'SEND_NOTIFICATION', error_kind: 'PERMANENT', order_id: ORDER_ID, page: '2', size: '20',
    });
    expect(screen.getByText('Página 2 de 3')).toBeTruthy();
  });

  it('an invalid order id is rejected without a request', async () => {
    renderAt('/admin/operaciones');
    await screen.findByText(/^#1 ·/);
    fireEvent.change(screen.getByLabelText('Id del pedido'), { target: { value: '12345' } });
    fireEvent.click(screen.getByRole('button', { name: 'Filtrar' }));
    expect(await screen.findByText(/debe ser un UUID completo/)).toBeTruthy();
    expect(api.callsTo(LIST)).toHaveLength(1);
    const view = renderAt('/admin/operaciones?pedido=nope');
    expect(await within(view.container).findByText(/no es válido; no se hizo la consulta/)).toBeTruthy();
    expect(api.callsTo(LIST)).toHaveLength(1);
  });

  it('retry is offered only for FAILED operations that nobody holds', async () => {
    const future = new Date(Date.now() + 60_000).toISOString();
    api.on('GET', '/api/admin/operations', () =>
      page([op(1), op(2, { status: 'PENDING', error_kind: null }), op(3, { status: 'DONE', error_kind: null }), op(4, { locked_until: future })]));
    renderAt('/admin/operaciones');
    await screen.findByText(/^#4 ·/);
    expect(within(rowOf(1)).getByRole('button', { name: 'Reintentar' })).toBeTruthy();
    for (const id of [2, 3, 4]) expect(within(rowOf(id)).queryByRole('button', { name: 'Reintentar' })).toBeNull();
    expect(within(rowOf(4)).getByText(/En proceso hasta/)).toBeTruthy();
  });

  it('a FAILED operation becomes retryable when its lock expires', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    api.on('GET', '/api/admin/operations', () => page([op(4, { locked_until: new Date(Date.now() + 5_000).toISOString() })]));
    renderAt('/admin/operaciones');
    await screen.findByText(/^#4 ·/);
    expect(within(rowOf(4)).queryByRole('button', { name: 'Reintentar' })).toBeNull();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_200);
    });
    expect(within(rowOf(4)).getByRole('button', { name: 'Reintentar' })).toBeTruthy();
    expect(within(rowOf(4)).queryByText(/En proceso hasta/)).toBeNull();
  });
});

describe('retry', () => {
  it.each(Object.entries(OUTCOME_TEXT) as [RetryOut['outcome'], string][])('outcome %s is reported and the row shows the returned operation', async (outcome, text) => {
    api.on('POST', '/api/admin/operations/1/retry', () =>
      json(200, { outcome, operation: op(1, { status: outcome === 'DONE' ? 'DONE' : 'FAILED', attempts: 4 }) } satisfies RetryOut));
    renderAt('/admin/operaciones');
    fireEvent.click(await screen.findByRole('button', { name: 'Reintentar' }));
    expect(await screen.findByText(text)).toBeTruthy();
    expect(within(rowOf(1)).getByText(/4 intentos/)).toBeTruthy();
  });

  it('PERMANENT asks for a confirmation that explains it will fail again without a fix', async () => {
    api.on('GET', '/api/admin/operations', () => page([op(1, { error_kind: 'PERMANENT' })]));
    api.on('POST', '/api/admin/operations/1/retry', () => json(200, { outcome: 'FAILED', operation: op(1, { error_kind: 'PERMANENT', attempts: 1 }) }));
    renderAt('/admin/operaciones');
    fireEvent.click(await screen.findByRole('button', { name: 'Reintentar' }));
    expect(screen.getByText(/Reintentarla sin corregir la causa volverá a fallar/)).toBeTruthy();
    expect(retries(1)).toHaveLength(0);
    fireEvent.click(screen.getByRole('button', { name: 'Cancelar' }));
    fireEvent.click(screen.getByRole('button', { name: 'Reintentar' }));
    fireEvent.click(screen.getByRole('button', { name: 'Sí, reintentar' }));
    expect(await screen.findByText(OUTCOME_TEXT.FAILED)).toBeTruthy();
    expect(retries(1)).toHaveLength(1);
  });

  it('claimed between the read and the click: 409 is final and the list is read again', async () => {
    api.on('POST', '/api/admin/operations/1/retry', () =>
      json(409, { detail: { code: 'OPERATION_NOT_RETRYABLE', message: 'x', status: 'FAILED', claimed: true } }));
    renderAt('/admin/operaciones');
    fireEvent.click(await screen.findByRole('button', { name: 'Reintentar' }));
    expect(await screen.findByText(/no la reintentó: otro proceso la está ejecutando ahora/)).toBeTruthy();
    await waitFor(() => expect(api.callsTo(LIST)).toHaveLength(2));
    expect(retries(1)).toHaveLength(1);
  });

  it('409 because it is no longer FAILED names the real state', async () => {
    api.on('POST', '/api/admin/operations/1/retry', () =>
      json(409, { detail: { code: 'OPERATION_NOT_RETRYABLE', message: 'x', status: 'DONE', claimed: false } }));
    renderAt('/admin/operaciones');
    fireEvent.click(await screen.findByRole('button', { name: 'Reintentar' }));
    expect(await screen.findByText(/ya no está fallida \(estado: completada\)/)).toBeTruthy();
  });

  it('timeout of a retry that did run: the real state is read back and nothing is sent again', async () => {
    api.on('POST', '/api/admin/operations/1/retry', networkError);
    api.on('GET', '/api/admin/operations', ({ url }) =>
      url.searchParams.get('order_id')
        ? page([op(1, { status: 'DONE', error_kind: null, attempts: 1, last_error: null, completed_at: '2026-10-23T10:01:00Z' })])
        : page([op(1)]));
    renderAt('/admin/operaciones');
    fireEvent.click(await screen.findByRole('button', { name: 'Reintentar' }));
    expect(await screen.findByText(/pudo ejecutarse\. Estado actual: completada, 1 intento\. No se volvió a enviar\./)).toBeTruthy();
    const readBack = api.callsTo(LIST).at(-1)!;
    expect(readBack.url.searchParams.get('order_id')).toBe(ORDER_ID);
    expect(retries(1)).toHaveLength(1);
    expect(within(rowOf(1)).queryByRole('button', { name: 'Reintentar' })).toBeNull();
  });

  it('a double click sends one retry', async () => {
    const slow = deferred();
    api.on('POST', '/api/admin/operations/1/retry', () => slow.promise);
    renderAt('/admin/operaciones');
    const button = await screen.findByRole('button', { name: 'Reintentar' });
    fireEvent.click(button);
    fireEvent.click(button);
    await act(async () => {
      slow.resolve(json(200, { outcome: 'DONE', operation: op(1, { status: 'DONE', error_kind: null }) }));
    });
    await screen.findByText(OUTCOME_TEXT.DONE);
    expect(retries(1)).toHaveLength(1);
  });
});

describe('diagnosis data', () => {
  it('is shown as text, without secret-looking fields or tokens, and limited in length', async () => {
    api.on('GET', '/api/admin/operations', () =>
      page([op(1, {
        last_error: `<script>alert(1)</script> Bearer eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiI3In0.c2ln x-internal-key=supersecret ${'x'.repeat(2500)}`,
        payload: { event_type: '<img src=x onerror=alert(2)>', amount: '10.00', api_key: 'k-123', nested: { password: 'p', ok: 1 } },
      })]));
    const { container } = renderAt('/admin/operaciones');
    await screen.findByText(/^#1 ·/);
    fireEvent.click(screen.getByText('Diagnóstico'));
    const pres = Array.from(container.querySelectorAll('.operation__pre')).map((p) => p.textContent ?? '');
    expect(pres[0]).toContain('<script>alert(1)</script>');
    expect(pres[0]).toContain('Bearer [oculto]');
    expect(pres[0]).toContain('x-internal-key=[oculto]');
    expect(pres[0]).not.toMatch(/eyJ|supersecret/);
    expect(pres[0]).toMatch(/caracteres más\)$/);
    expect(pres[1]).toContain('"api_key": "[oculto]"');
    expect(pres[1]).toContain('"password": "[oculto]"');
    expect(pres[1]).toContain('"amount": "10.00"');
    expect(pres[1]).toContain('<img src=x onerror=alert(2)>');
    expect(container.querySelector('.operation script, .operation img')).toBeNull();
  });
});

describe('authorization', () => {
  it('403 checks the session again; a demoted admin is signed out without more admin requests', async () => {
    renderAt('/admin/operaciones');
    await screen.findByText(/^#1 ·/);
    api.on('POST', '/api/admin/operations/1/retry', () => json(403, { detail: 'Admin role required' }));
    api.on('GET', '/api/users/me', () => json(200, { ...admin, role: 'customer' }));
    fireEvent.click(screen.getByRole('button', { name: 'Reintentar' }));
    expect(await screen.findByText('Tus permisos cambiaron. Inicia sesión de nuevo para continuar.')).toBeTruthy();
    const before = api.callsTo(LIST).length + retries(1).length;
    await new Promise((r) => setTimeout(r, 50));
    expect(api.callsTo(LIST).length + retries(1).length).toBe(before);
  });
});
