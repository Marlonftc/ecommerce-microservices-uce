// @vitest-environment jsdom
// Pending operations of the saga for an admin: filters, retry rules decided by order-service,
// locks, outcomes, unknown outcomes and the safe display of diagnosis data.
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, useLocation, useNavigate } from 'react-router-dom';
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

function HistoryControls() {
  const navigate = useNavigate();
  const location = useLocation();
  return <div>
    <button onClick={() => navigate(-1)}>Atrás</button>
    <button onClick={() => navigate(1)}>Adelante</button>
    <output data-testid="operations-location">{location.pathname}{location.search}</output>
  </div>;
}

function renderAt(path: string, previous?: string) {
  return render(
    <MemoryRouter initialEntries={previous ? [previous, path] : [path]}>
      <AuthProvider>
        <AppRoutes />
        <HistoryControls />
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
const page = (items: OperationOut[], total = items.length, n = 1) => json(200, { items, total, page: n, size: 20 });
const retries = (id: number) => api.callsTo(`POST /api/admin/operations/${id}/retry`);
const rowOf = (id: number) => screen.getByText(new RegExp(`^#${id} ·`)).closest('li') as HTMLElement;
const locationQuery = () => new URLSearchParams(screen.getByTestId('operations-location').textContent!.split('?')[1]);
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
  it.each(Object.entries(OUTCOME_TEXT) as [RetryOut['outcome'], string][])('outcome %s is reported and the list is read again', async (outcome, text) => {
    let current = op(1);
    api.on('GET', '/api/admin/operations', () => page([current]));
    api.on('POST', '/api/admin/operations/1/retry', () => {
      current = op(1, { status: outcome === 'DONE' ? 'DONE' : 'FAILED', attempts: 4 });
      return json(200, { outcome, operation: current } satisfies RetryOut);
    });
    renderAt('/admin/operaciones');
    fireEvent.click(await screen.findByRole('button', { name: 'Reintentar' }));
    expect(await screen.findByText(text)).toBeTruthy();
    expect(await screen.findByText(/4 intentos/)).toBeTruthy();
    expect(api.callsTo(LIST)).toHaveLength(2);
    expect(retries(1)).toHaveLength(1);
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
    let attempted = false;
    api.on('POST', '/api/admin/operations/1/retry', () => { attempted = true; return networkError(); });
    api.on('GET', '/api/admin/operations', ({ url }) =>
      url.searchParams.get('order_id') || attempted
        ? page([op(1, { status: 'DONE', error_kind: null, attempts: 1, last_error: null, completed_at: '2026-10-23T10:01:00Z' })])
        : page([op(1)]));
    renderAt('/admin/operaciones');
    fireEvent.click(await screen.findByRole('button', { name: 'Reintentar' }));
    expect(await screen.findByText(/pudo ejecutarse\. Estado actual: completada, 1 intento\. No se volvió a enviar\./)).toBeTruthy();
    await waitFor(() => expect(api.callsTo(LIST)).toHaveLength(3));
    const readBack = api.callsTo(LIST).find((call) => call.url.searchParams.get('size') === '100')!;
    expect(readBack.url.searchParams.get('order_id')).toBe(ORDER_ID);
    expect(retries(1)).toHaveLength(1);
    await screen.findByText(/^#1 ·/);
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

describe('filtered list after a manual retry', () => {
  const filters = `estado=FAILED&tipo=SEND_NOTIFICATION&error=TRANSIENT&pedido=${ORDER_ID}`;

  it('reloads rows and total while preserving every filter and a valid page', async () => {
    let attempted = false;
    api.on('GET', '/api/admin/operations', ({ url }) =>
      page([op(attempted ? 2 : 1)], attempted ? 41 : 42, Number(url.searchParams.get('page'))));
    api.on('POST', '/api/admin/operations/1/retry', () => {
      attempted = true;
      return json(200, { outcome: 'DONE', operation: op(1, { status: 'DONE' }) });
    });
    renderAt(`/admin/operaciones?${filters}&pagina=2`);
    fireEvent.click(await screen.findByRole('button', { name: 'Reintentar' }));
    await screen.findByText(/^#2 ·/);
    expect(screen.queryByText(/^#1 ·/)).toBeNull();
    expect(screen.getByText('41 operaciones')).toBeTruthy();
    expect(screen.getByText('Página 2 de 3')).toBeTruthy();
    expect(screen.getByText(OUTCOME_TEXT.DONE)).toBeTruthy();
    expect(api.callsTo(LIST)).toHaveLength(2);
    expect(Object.fromEntries(api.callsTo(LIST)[1]!.url.searchParams)).toEqual({
      status: 'FAILED', kind: 'SEND_NOTIFICATION', error_kind: 'TRANSIENT', order_id: ORDER_ID, page: '2', size: '20',
    });
    expect(retries(1)).toHaveLength(1);
  });

  it('replaces a page that disappeared with the last valid one, preserving filters and history', async () => {
    let attempted = false;
    api.on('GET', '/api/admin/operations', ({ url }) => {
      if (url.searchParams.get('status') === 'DONE') return page([op(50, { status: 'DONE' })]);
      const n = Number(url.searchParams.get('page'));
      return attempted
        ? page(n === 1 ? Array.from({ length: 20 }, (_, i) => op(i + 2)) : [], 20, n)
        : page([op(1)], 21, n);
    });
    api.on('POST', '/api/admin/operations/1/retry', () => {
      attempted = true;
      return json(200, { outcome: 'DONE', operation: op(1, { status: 'DONE' }) });
    });
    renderAt(`/admin/operaciones?${filters}&pagina=2`, '/admin/operaciones?estado=DONE');
    fireEvent.click(await screen.findByRole('button', { name: 'Reintentar' }));
    await screen.findByText(/^#2 ·/);
    expect(locationQuery().get('pagina')).toBeNull();
    expect(locationQuery().get('estado')).toBe('FAILED');
    expect(locationQuery().get('tipo')).toBe('SEND_NOTIFICATION');
    expect(locationQuery().get('error')).toBe('TRANSIENT');
    expect(locationQuery().get('pedido')).toBe(ORDER_ID);
    expect(screen.getByText('20 operaciones')).toBeTruthy();
    expect(screen.getByText(OUTCOME_TEXT.DONE)).toBeTruthy();
    expect(api.callsTo(LIST).map((call) => call.url.searchParams.get('page'))).toEqual(['2', '2', '1']);
    fireEvent.click(screen.getByRole('button', { name: 'Atrás' }));
    await screen.findByText(/^#50 ·/);
    expect((screen.getByLabelText('Estado') as HTMLSelectElement).value).toBe('DONE');
    fireEvent.click(screen.getByRole('button', { name: 'Adelante' }));
    await screen.findByText(/^#2 ·/);
    expect(locationQuery().get('pagina')).toBeNull();
    expect((screen.getByLabelText('Estado') as HTMLSelectElement).value).toBe('FAILED');
    expect(api.callsTo(LIST).filter((call) => call.url.searchParams.get('page') === '2')).toHaveLength(2);
    expect(retries(1)).toHaveLength(1);
  });

  it('keeps the success notice when no matching operations remain and normalizes the page', async () => {
    let attempted = false;
    api.on('GET', '/api/admin/operations', ({ url }) =>
      page(attempted ? [] : [op(1)], attempted ? 0 : 41, Number(url.searchParams.get('page'))));
    api.on('POST', '/api/admin/operations/1/retry', () => {
      attempted = true;
      return json(200, { outcome: 'DONE', operation: op(1, { status: 'DONE' }) });
    });
    renderAt(`/admin/operaciones?${filters}&pagina=3`);
    fireEvent.click(await screen.findByRole('button', { name: 'Reintentar' }));
    expect(await screen.findByText('No hay operaciones con estos filtros')).toBeTruthy();
    expect(screen.getByText(OUTCOME_TEXT.DONE)).toBeTruthy();
    expect(screen.getByText('Operación #1')).toBeTruthy();
    expect(screen.queryByRole('list', { name: 'Operaciones' })).toBeNull();
    expect(locationQuery().get('pagina')).toBeNull();
    expect(locationQuery().get('estado')).toBe('FAILED');
    expect(api.callsTo(LIST).map((call) => call.url.searchParams.get('page'))).toEqual(['3', '3', '1']);
    expect(retries(1)).toHaveLength(1);
  });

  it('checks an uncertain outcome, then reloads the filtered total without inserting a completed row', async () => {
    let attempted = false;
    api.on('GET', '/api/admin/operations', ({ url }) =>
      url.searchParams.get('size') === '100'
        ? page([op(1, { status: 'DONE', attempts: 1 })])
        : page(attempted ? [] : [op(1)]));
    api.on('POST', '/api/admin/operations/1/retry', () => { attempted = true; return networkError(); });
    renderAt(`/admin/operaciones?${filters}`);
    fireEvent.click(await screen.findByRole('button', { name: 'Reintentar' }));
    expect(await screen.findByText('No hay operaciones con estos filtros')).toBeTruthy();
    expect(screen.getByText(/Estado actual: completada, 1 intento/)).toBeTruthy();
    expect(screen.queryByText(/^#1 ·/)).toBeNull();
    expect(api.callsTo(LIST)).toHaveLength(3);
    expect(api.callsTo(LIST)[1]!.url.searchParams.get('size')).toBe('100');
    expect(api.callsTo(LIST)[2]!.url.searchParams.get('status')).toBe('FAILED');
    expect(api.callsTo(LIST)[2]!.url.searchParams.get('order_id')).toBe(ORDER_ID);
    expect(retries(1)).toHaveLength(1);
  });

  it('a failed refresh hides stale rows and retrying the read never repeats the operation', async () => {
    api.on('POST', '/api/admin/operations/1/retry', () => {
      api.once('GET', '/api/admin/operations', () => json(503, { detail: 'Unavailable' }));
      api.on('GET', '/api/admin/operations', () => page([]));
      return json(200, { outcome: 'DONE', operation: op(1, { status: 'DONE' }) });
    });
    renderAt('/admin/operaciones?estado=FAILED');
    fireEvent.click(await screen.findByRole('button', { name: 'Reintentar' }));
    expect(await screen.findByText('No pudimos cargar las operaciones.')).toBeTruthy();
    expect(screen.getByText(OUTCOME_TEXT.DONE)).toBeTruthy();
    expect(screen.queryByText(/^#1 ·/)).toBeNull();
    expect(retries(1)).toHaveLength(1);
    fireEvent.click(screen.getByRole('button', { name: 'Reintentar' }));
    expect(await screen.findByText('No hay operaciones con estos filtros')).toBeTruthy();
    expect(api.callsTo(LIST)).toHaveLength(3);
    expect(retries(1)).toHaveLength(1);
  });

  it('refreshes the page reached during a pending retry and ignores its older GET response', async () => {
    const oldRead = deferred();
    const retryAnswer = deferred();
    let settled = false;
    api.on('GET', '/api/admin/operations', ({ url }) => url.searchParams.get('page') === '2'
      ? settled ? page([op(22)], 30, 2) : oldRead.promise
      : page([op(1)], 40));
    api.on('POST', '/api/admin/operations/1/retry', () => retryAnswer.promise);
    renderAt('/admin/operaciones?estado=FAILED');
    fireEvent.click(await screen.findByRole('button', { name: 'Reintentar' }));
    fireEvent.click(screen.getByRole('link', { name: 'Siguiente' }));
    await waitFor(() => expect(api.callsTo(LIST)).toHaveLength(2));
    await act(async () => {
      settled = true;
      retryAnswer.resolve(json(200, { outcome: 'DONE', operation: op(1, { status: 'DONE' }) }));
    });
    await screen.findByText(/^#22 ·/);
    expect(screen.getByText('Página 2 de 2')).toBeTruthy();
    expect(screen.getByText('30 operaciones')).toBeTruthy();
    expect(api.callsTo(LIST)[1]!.init!.signal!.aborted).toBe(true);
    expect(api.callsTo(LIST).at(-1)!.url.searchParams.get('page')).toBe('2');
    expect(api.callsTo(LIST).at(-1)!.url.searchParams.get('status')).toBe('FAILED');
    await act(async () => { oldRead.resolve(page([op(99)], 99, 2)); });
    expect(screen.queryByText(/^#99 ·/)).toBeNull();
    expect(screen.getByText('30 operaciones')).toBeTruthy();
    expect(retries(1)).toHaveLength(1);
  });
});

describe('filter and page navigation', () => {
  it('restores filters, page and validation state with Back and Forward', async () => {
    api.on('GET', '/api/admin/operations', ({ url }) =>
      page([op(url.searchParams.get('status') === 'DONE' ? 2 : 1)], 45, Number(url.searchParams.get('page'))));
    renderAt('/admin/operaciones?estado=FAILED&pagina=2');
    await screen.findByText(/^#1 ·/);
    fireEvent.change(screen.getByLabelText('Estado'), { target: { value: 'DONE' } });
    fireEvent.change(screen.getByLabelText('Tipo'), { target: { value: 'RESOLVE_PAYMENT' } });
    fireEvent.change(screen.getByLabelText('Tipo de error'), { target: { value: 'PERMANENT' } });
    fireEvent.change(screen.getByLabelText('Id del pedido'), { target: { value: ORDER_ID } });
    fireEvent.click(screen.getByRole('button', { name: 'Filtrar' }));
    await screen.findByText(/^#2 ·/);
    expect(locationQuery().get('pagina')).toBeNull();
    fireEvent.click(screen.getByRole('link', { name: 'Siguiente' }));
    await waitFor(() => expect(locationQuery().get('pagina')).toBe('2'));
    fireEvent.click(screen.getByRole('button', { name: 'Atrás' }));
    await waitFor(() => expect(locationQuery().get('pagina')).toBeNull());
    fireEvent.change(screen.getByLabelText('Id del pedido'), { target: { value: 'invalid' } });
    fireEvent.click(screen.getByRole('button', { name: 'Filtrar' }));
    expect(screen.getByText(/debe ser un UUID completo/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Atrás' }));
    await screen.findByText(/^#1 ·/);
    expect(locationQuery().get('pagina')).toBe('2');
    expect((screen.getByLabelText('Estado') as HTMLSelectElement).value).toBe('FAILED');
    for (const label of ['Tipo', 'Tipo de error', 'Id del pedido']) expect((screen.getByLabelText(label) as HTMLInputElement).value).toBe('');
    expect(screen.queryByText(/debe ser un UUID completo/)).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Adelante' }));
    await screen.findByText(/^#2 ·/);
    expect(locationQuery().get('pagina')).toBeNull();
    expect((screen.getByLabelText('Estado') as HTMLSelectElement).value).toBe('DONE');
    expect((screen.getByLabelText('Tipo') as HTMLSelectElement).value).toBe('RESOLVE_PAYMENT');
    expect((screen.getByLabelText('Tipo de error') as HTMLSelectElement).value).toBe('PERMANENT');
    expect((screen.getByLabelText('Id del pedido') as HTMLInputElement).value).toBe(ORDER_ID);
    expect(api.calls.some((call) => call.key.startsWith('POST'))).toBe(false);
  });

  it('opens the last available page for an out-of-range URL without losing the filters', async () => {
    api.on('GET', '/api/admin/operations', ({ url }) => {
      const n = Number(url.searchParams.get('page'));
      return page(n === 2 ? [op(21)] : [], 21, n);
    });
    renderAt('/admin/operaciones?estado=FAILED&tipo=SEND_NOTIFICATION&pagina=5');
    await screen.findByText(/^#21 ·/);
    expect(locationQuery().get('pagina')).toBe('2');
    expect(locationQuery().get('estado')).toBe('FAILED');
    expect(locationQuery().get('tipo')).toBe('SEND_NOTIFICATION');
    expect(screen.getByText('Página 2 de 2')).toBeTruthy();
    expect(api.callsTo(LIST).map((call) => call.url.searchParams.get('page'))).toEqual(['5', '2']);
  });

  it('applying the same filters refreshes GET data without retrying any operation', async () => {
    let current = op(1);
    api.on('GET', '/api/admin/operations', () => page([current]));
    renderAt('/admin/operaciones?estado=FAILED');
    await screen.findByText(/^#1 ·/);
    current = op(1, { attempts: 9 });
    fireEvent.click(screen.getByRole('button', { name: 'Filtrar' }));
    expect(await screen.findByText(/9 intentos/)).toBeTruthy();
    expect(api.callsTo(LIST)).toHaveLength(2);
    expect(retries(1)).toHaveLength(0);
  });

  it('leaving the page during a retry prevents a late response from issuing more reads', async () => {
    const pending = deferred();
    api.on('POST', '/api/admin/operations/1/retry', () => pending.promise);
    renderAt('/admin/operaciones');
    fireEvent.click(await screen.findByRole('button', { name: 'Reintentar' }));
    fireEvent.click(screen.getByRole('link', { name: 'Mi cuenta' }));
    await screen.findByRole('heading', { name: 'Mi cuenta' });
    await act(async () => { pending.resolve(json(200, { outcome: 'DONE', operation: op(1, { status: 'DONE' }) })); });
    expect(api.callsTo(LIST)).toHaveLength(1);
    expect(retries(1)).toHaveLength(1);
    expect(screen.queryByText(OUTCOME_TEXT.DONE)).toBeNull();
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
