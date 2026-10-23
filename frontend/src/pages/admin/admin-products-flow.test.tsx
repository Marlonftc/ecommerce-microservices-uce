// @vitest-environment jsdom
// Products and inventory for an admin, with the real routes and session; fetch answers
// like product-service and inventory-service behind the gateway.
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AppRoutes } from '../../App';
import type { ProductOut } from '../../api/types';
import { AuthProvider } from '../../auth/AuthContext';
import { INVENTORY_PRODUCT_SERVICE_DOWN, KEY_REUSED, NOT_ENOUGH_UNITS, NO_STOCK_RECORD } from '../../admin/adminErrors';
import { getPendingAdjustment, savePendingAdjustment } from '../../admin/pendingAdjustments';
import { createFakeApi, deferred, json, type Call, type FakeApi } from '../../test/fakeApi';
import { admin, cart, customer, product, signIn } from '../../test/shop';

let api: FakeApi;
const ID = 42;
const T0 = '2026-10-23T10:00:00Z';
const T1 = '2026-10-23T10:05:00Z';
const otherAdmin = { ...admin, id: 2, email: 'admin2@example.com', full_name: 'Admin Dos' };

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <AuthProvider>
        <AppRoutes />
      </AuthProvider>
    </MemoryRouter>,
  );
}

const networkError = () => {
  throw new TypeError('Failed to fetch');
};
const gateway502 = () => json(502, { detail: { code: 'UPSTREAM_UNAVAILABLE', message: 'The service is not available' } });
const saved = (overrides: Partial<ProductOut> = {}) =>
  product(ID, { sku: 'LAP-003', name: 'Laptop Pro', description: 'Equipo', category: 'laptops', price: '899.90', updated_at: T0, ...overrides });
const stockRecord = (available: number, reserved = 0) => ({ product_id: ID, available, reserved, updated_at: T0 });
const adjustment = (delta: number, before: number, id = 1) => ({
  id, product_id: ID, delta, available_before: before, available_after: before + delta, admin_user_id: 1, reason: '', created_at: T1,
});
const keyOf = (call: Call) => (call.init?.headers as Record<string, string>)['Idempotency-Key'];
const adjustCalls = () => api.callsTo(`POST /api/inventory/${ID}/adjustments`);
const isCategoryScan = (call: Call) => call.url.searchParams.get('include_inactive') === null && call.url.searchParams.get('q') === null;

/** Product page of an admin: product, stock record and an empty history */
function serveProductPage(options: { product?: ProductOut; stock?: () => Response } = {}) {
  api.on('GET', `/api/products/${ID}`, () => json(200, options.product ?? saved()));
  api.on('GET', `/api/inventory/${ID}`, options.stock ?? (() => json(200, stockRecord(5))));
  api.on('GET', `/api/inventory/${ID}/adjustments`, () => json(200, []));
}

async function fillAndAdjust(mode: 'Agregar unidades' | 'Quitar unidades', units: string, reason = '') {
  fireEvent.click(await screen.findByRole('radio', { name: mode }));
  fireEvent.change(screen.getByLabelText('Unidades'), { target: { value: units } });
  if (reason) fireEvent.change(screen.getByLabelText('Motivo (opcional)'), { target: { value: reason } });
  fireEvent.click(screen.getByRole('button', { name: 'Aplicar ajuste' }));
}

function fillProduct(values: { sku?: string; name?: string; category?: string; price?: string }) {
  if (values.sku !== undefined) fireEvent.change(screen.getByLabelText('SKU'), { target: { value: values.sku } });
  if (values.name !== undefined) fireEvent.change(screen.getByLabelText('Nombre'), { target: { value: values.name } });
  if (values.category !== undefined) fireEvent.change(screen.getByLabelText('Categoría'), { target: { value: values.category } });
  if (values.price !== undefined) fireEvent.change(screen.getByLabelText('Precio (USD)'), { target: { value: values.price } });
}

const NEW = { sku: 'lap-003', name: 'Laptop Pro', category: 'Laptops', price: '899,9' };
const SENT = { sku: 'LAP-003', name: 'Laptop Pro', description: '', category: 'laptops', price: '899.90' };

beforeEach(() => {
  api = createFakeApi();
  vi.stubGlobal('fetch', api.fetchMock);
  sessionStorage.clear();
  signIn(api, admin);
  api.on('GET', '/api/cart', () => json(200, cart(1)));
  api.on('GET', '/api/products', () => json(200, { items: [], total: 0, limit: 100, offset: 0 }));
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('products list', () => {
  it('active and inactive products, search and pages of 20; the stock of the page in ONE request', async () => {
    const items = [
      product(1, { name: 'Laptop A' }),
      product(2, { name: 'Laptop B', is_active: false }),
      product(3, { name: 'Laptop C' }),
    ];
    api.on('GET', '/api/products', ({ url }) => json(200, { items, total: 45, limit: 20, offset: Number(url.searchParams.get('offset')) }));
    api.on('GET', '/api/inventory', () => json(200, [{ product_id: 1, available: 7, reserved: 2, updated_at: T0 }, { product_id: 2, available: 0, reserved: 0, updated_at: T0 }]));
    renderAt('/admin/productos?q=laptop&pagina=2');
    expect(await screen.findByText('Laptop B')).toBeTruthy();
    const call = api.callsTo('GET /api/products').at(-1)!;
    expect([call.url.searchParams.get('include_inactive'), call.url.searchParams.get('q'), call.url.searchParams.get('limit'), call.url.searchParams.get('offset')])
      .toEqual(['true', 'laptop', '20', '20']);
    await waitFor(() => expect(screen.getByText(/7 disp\./)).toBeTruthy());
    const rows = screen.getAllByRole('row');
    expect(within(rows[2]!).getByText('Inactivo')).toBeTruthy();
    expect(within(rows[2]!).getByText(/0 disp\./)).toBeTruthy();
    expect(within(rows[3]!).getByText('Sin registro')).toBeTruthy();
    expect(screen.getByText(/2 reserv\./)).toBeTruthy();
    expect(api.callsTo('GET /api/inventory')).toHaveLength(1);
    expect(api.callsTo('GET /api/inventory')[0]!.url.searchParams.get('ids')).toBe('1,2,3');
    expect(screen.getByText('Página 2 de 3')).toBeTruthy();
  });

  it('inventory-service down: the products are still listed', async () => {
    api.on('GET', '/api/products', () => json(200, { items: [product(1, { name: 'Laptop A' })], total: 1, limit: 20, offset: 0 }));
    api.on('GET', '/api/inventory', gateway502);
    renderAt('/admin/productos');
    expect(await screen.findByText(/Las existencias no están disponibles/)).toBeTruthy();
    expect(screen.getByText('Laptop A')).toBeTruthy();
    expect(screen.getByText('No disponible')).toBeTruthy();
  });

  it('a customer gets the forbidden page on every admin route, without admin requests', async () => {
    signIn(api, customer);
    for (const path of ['/admin/productos', '/admin/productos/nuevo', `/admin/productos/${ID}`]) {
      const view = renderAt(path);
      expect(await screen.findByRole('heading', { name: 'No tienes permiso para ver esta página' })).toBeTruthy();
      view.unmount();
    }
    expect(api.calls.some((c) => c.url.searchParams.get('include_inactive') === 'true')).toBe(false);
    expect(api.calls.some((c) => c.key.startsWith('POST') || c.key.startsWith('PUT') || c.key.startsWith('DELETE'))).toBe(false);
    expect(api.callsTo(`GET /api/inventory/${ID}`)).toHaveLength(0);
  });
});

describe('new product', () => {
  it('validates before sending and sends the price as exact text', async () => {
    api.on('POST', '/api/products', ({ body }) => json(201, { ...saved(), ...(body as object) }));
    serveProductPage();
    renderAt('/admin/productos/nuevo');
    await screen.findByRole('heading', { name: 'Nuevo producto' });
    fillProduct({ sku: 'LÁP 3', name: 'L', category: 'x', price: '1.234' });
    fireEvent.click(screen.getByRole('button', { name: 'Crear producto' }));
    expect(await screen.findByText(/Formato ambiguo/)).toBeTruthy();
    expect(screen.getByText(/El nombre debe tener/)).toBeTruthy();
    expect(api.callsTo('POST /api/products')).toHaveLength(0);

    fillProduct({ ...NEW, price: '0,1' });
    expect(screen.getByText('Se guardará como LAP-003')).toBeTruthy();
    expect(screen.getByText('Se guardará como laptops')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Crear producto' }));
    expect(await screen.findByText('Producto creado. Registra sus existencias en el inventario.')).toBeTruthy();
    expect(api.callsTo('POST /api/products')[0]!.body).toEqual({ ...SENT, price: '0.10' });
    expect(typeof (api.callsTo('POST /api/products')[0]!.body as { price: unknown }).price).toBe('string');
  });

  it('422 from the server is shown on the field; 409 says the SKU exists', async () => {
    api.once('POST', '/api/products', () => json(422, { detail: [{ loc: ['body', 'price'], msg: 'Decimal input should have no more than 12 digits' }] }));
    api.once('POST', '/api/products', () => json(409, { detail: 'SKU already exists' }));
    renderAt('/admin/productos/nuevo');
    await screen.findByRole('heading', { name: 'Nuevo producto' });
    fillProduct(NEW);
    fireEvent.click(screen.getByRole('button', { name: 'Crear producto' }));
    expect(await screen.findByText(/El servidor rechazó este valor: Decimal input/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Crear producto' }));
    expect(await screen.findByText('Ya existe un producto con este SKU.')).toBeTruthy();
  });

  it('lost answer, the SKU exists with the same data: "it was created", without a second POST', async () => {
    api.on('POST', '/api/products', networkError);
    api.on('GET', '/api/products', (call) =>
      isCategoryScan(call)
        ? json(200, { items: [], total: 0, limit: 100, offset: 0 })
        : json(200, { items: [product(7, { sku: 'OTHER-1', name: 'Laptop Pro Max' }), saved({ description: '' })], total: 2, limit: 100, offset: 0 }));
    serveProductPage();
    renderAt('/admin/productos/nuevo');
    await screen.findByRole('heading', { name: 'Nuevo producto' });
    fillProduct(NEW);
    fireEvent.click(screen.getByRole('button', { name: 'Crear producto' }));
    expect(await screen.findByText('El producto sí se había creado: no se volvió a enviar.')).toBeTruthy();
    expect(api.callsTo('POST /api/products')).toHaveLength(1);
    const search = api.callsTo('GET /api/products').find((c) => c.url.searchParams.get('q'))!;
    expect([search.url.searchParams.get('q'), search.url.searchParams.get('include_inactive')]).toEqual(['Laptop Pro', 'true']);
  });

  it('lost answer, the SKU exists with other data: it is not claimed as created', async () => {
    api.on('POST', '/api/products', gateway502);
    api.on('GET', '/api/products', (call) =>
      isCategoryScan(call)
        ? json(200, { items: [], total: 0, limit: 100, offset: 0 })
        : json(200, { items: [saved({ id: 9, price: '10.00' })], total: 1, limit: 100, offset: 0 }));
    renderAt('/admin/productos/nuevo');
    await screen.findByRole('heading', { name: 'Nuevo producto' });
    fillProduct(NEW);
    fireEvent.click(screen.getByRole('button', { name: 'Crear producto' }));
    expect(await screen.findByText('Ya existe un producto con el SKU LAP-003.')).toBeTruthy();
    expect(screen.queryByText(/sí se había creado/)).toBeNull();
  });

  it('incomplete search: the outcome stays unknown; a controlled retry that gets 409 is checked again', async () => {
    const page = Array.from({ length: 100 }, (_, i) => product(100 + i, { sku: `LP-${i}`, name: `Laptop Pro ${i}` }));
    api.once('POST', '/api/products', networkError);
    api.on('POST', '/api/products', () => json(409, { detail: 'SKU already exists' }));
    api.on('GET', '/api/products', (call) =>
      isCategoryScan(call)
        ? json(200, { items: [], total: 0, limit: 100, offset: 0 })
        : json(200, { items: page, total: 900, limit: 100, offset: Number(call.url.searchParams.get('offset')) }));
    renderAt('/admin/productos/nuevo');
    await screen.findByRole('heading', { name: 'Nuevo producto' });
    fillProduct(NEW);
    fireEvent.click(screen.getByRole('button', { name: 'Crear producto' }));
    expect(await screen.findByText('No sabemos si el producto se creó.')).toBeTruthy();
    expect(screen.getByText(/más resultados de los que se pudieron revisar/)).toBeTruthy();
    expect((screen.getByLabelText('SKU') as HTMLInputElement).closest('fieldset')!.disabled).toBe(true);
    const searches = () => api.callsTo('GET /api/products').filter((c) => c.url.searchParams.get('q'));
    expect(searches()).toHaveLength(3);

    fireEvent.click(screen.getByRole('button', { name: 'Intentar crear de nuevo' }));
    await waitFor(() => expect(searches()).toHaveLength(6));
    expect(await screen.findByText('No sabemos si el producto se creó.')).toBeTruthy();
    expect(api.callsTo('POST /api/products')).toHaveLength(2);
    expect(screen.queryByText(/sí se había creado|Ya existe un producto con este SKU\./)).toBeNull();
  });

  it('complete search without that SKU: "it was not created" and a new POST is allowed', async () => {
    api.once('POST', '/api/products', () => json(504, { detail: { code: 'UPSTREAM_TIMEOUT', message: 'x' } }));
    api.on('POST', '/api/products', () => json(201, saved()));
    serveProductPage();
    renderAt('/admin/productos/nuevo');
    await screen.findByRole('heading', { name: 'Nuevo producto' });
    fillProduct(NEW);
    fireEvent.click(screen.getByRole('button', { name: 'Crear producto' }));
    expect(await screen.findByText('El producto no se creó.')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Crear de nuevo' }));
    expect(await screen.findByText('Producto creado. Registra sus existencias en el inventario.')).toBeTruthy();
    expect(api.callsTo('POST /api/products').map((c) => c.body)).toEqual([SENT, SENT]);
  });
});

describe('edit product', () => {
  it('another admin changed it (updated_at): no PUT until the current data is loaded', async () => {
    serveProductPage();
    renderAt(`/admin/productos/${ID}`);
    await screen.findByRole('heading', { name: 'Laptop Pro' });
    api.on('GET', `/api/products/${ID}`, () => json(200, saved({ name: 'Laptop Pro 2026', updated_at: T1 })));
    api.on('PUT', `/api/products/${ID}`, ({ body }) => json(200, { ...saved(), ...(body as object), updated_at: '2026-10-23T10:10:00Z' }));
    fillProduct({ price: '950' });
    fireEvent.click(screen.getByRole('button', { name: 'Guardar cambios' }));
    expect(await screen.findByText('Otro administrador modificó este producto mientras lo editabas.')).toBeTruthy();
    expect(api.callsTo(`PUT /api/products/${ID}`)).toHaveLength(0);
    expect((screen.getByRole('button', { name: 'Guardar cambios' }) as HTMLButtonElement).disabled).toBe(true);

    fireEvent.click(screen.getByRole('button', { name: 'Cargar datos actuales' }));
    expect((screen.getByLabelText('Nombre') as HTMLInputElement).value).toBe('Laptop Pro 2026');
    fillProduct({ price: '950' });
    fireEvent.click(screen.getByRole('button', { name: 'Guardar cambios' }));
    expect(await screen.findByText('Cambios guardados.')).toBeTruthy();
    expect(api.callsTo(`PUT /api/products/${ID}`)[0]!.body).toEqual({
      sku: 'LAP-003', name: 'Laptop Pro 2026', description: 'Equipo', category: 'laptops', price: '950.00', is_active: true,
    });
  });

  it('a save whose answer was lost is detected on the next save, without a second PUT', async () => {
    serveProductPage();
    renderAt(`/admin/productos/${ID}`);
    await screen.findByRole('heading', { name: 'Laptop Pro' });
    api.once('PUT', `/api/products/${ID}`, networkError);
    fillProduct({ price: '950' });
    fireEvent.click(screen.getByRole('button', { name: 'Guardar cambios' }));
    expect(await screen.findByText(/No sabemos si los cambios se guardaron/)).toBeTruthy();
    // It had been saved: the product now holds the new price
    api.on('GET', `/api/products/${ID}`, () => json(200, saved({ price: '950.00', updated_at: T1 })));
    fireEvent.click(screen.getByRole('button', { name: 'Guardar cambios' }));
    expect(await screen.findByText('Los datos guardados ya coinciden con el formulario.')).toBeTruthy();
    expect(api.callsTo(`PUT /api/products/${ID}`)).toHaveLength(1);
  });

  it('deactivates after an inline confirmation and reactivates with PUT is_active=true; the inventory is kept', async () => {
    serveProductPage({ stock: () => json(200, stockRecord(9, 1)) });
    api.on('DELETE', `/api/products/${ID}`, () => {
      api.on('GET', `/api/products/${ID}`, () => json(200, saved({ is_active: false, updated_at: T1 })));
      return new Response(null, { status: 204 });
    });
    renderAt(`/admin/productos/${ID}`);
    await screen.findByRole('heading', { name: 'Laptop Pro' });
    fireEvent.click(screen.getByRole('button', { name: 'Desactivar producto' }));
    expect(screen.getByText(/Su inventario y los pedidos existentes se conservan/)).toBeTruthy();
    expect(api.callsTo(`DELETE /api/products/${ID}`)).toHaveLength(0);
    fireEvent.click(screen.getByRole('button', { name: 'Sí, desactivar' }));
    expect(await screen.findByText(/Producto desactivado: ya no aparece en el catálogo/)).toBeTruthy();
    expect(screen.getByText('Inactivo')).toBeTruthy();

    api.on('PUT', `/api/products/${ID}`, ({ body }) => {
      api.on('GET', `/api/products/${ID}`, () => json(200, saved({ is_active: true, updated_at: '2026-10-23T10:10:00Z' })));
      return json(200, { ...saved(), ...(body as object) });
    });
    fireEvent.click(screen.getByRole('button', { name: 'Reactivar producto' }));
    expect(await screen.findByText(/Producto reactivado/)).toBeTruthy();
    expect(api.callsTo(`PUT /api/products/${ID}`)[0]!.body).toEqual({
      sku: 'LAP-003', name: 'Laptop Pro', description: 'Equipo', category: 'laptops', price: '899.90', is_active: true,
    });
    expect(screen.getByText('9')).toBeTruthy(); // available units untouched
    expect(adjustCalls()).toHaveLength(0);
  });
});

describe('inventory', () => {
  it('record with units, record with 0 units, no record and inventory unavailable are different', async () => {
    serveProductPage({ stock: () => json(200, stockRecord(0)) });
    let view = renderAt(`/admin/productos/${ID}`);
    expect(await screen.findByText(/Registro con 0 unidades disponibles/)).toBeTruthy();
    expect(screen.queryByText('Sin registro de inventario')).toBeNull();
    view.unmount();

    serveProductPage({ stock: () => json(404, { detail: NO_STOCK_RECORD }) });
    view = renderAt(`/admin/productos/${ID}`);
    expect(await screen.findByText('Sin registro de inventario')).toBeTruthy();
    expect(screen.queryByText(/Registro con 0 unidades/)).toBeNull();
    view.unmount();

    serveProductPage({ stock: gateway502 });
    renderAt(`/admin/productos/${ID}`);
    expect(await screen.findByText('Inventario no disponible en este momento.')).toBeTruthy();
    expect(screen.queryByText('Sin registro de inventario')).toBeNull();
  });

  it('applies a relative adjustment, then reads the stock and the history again', async () => {
    serveProductPage();
    api.on('POST', `/api/inventory/${ID}/adjustments`, () => json(201, adjustment(3, 5)));
    renderAt(`/admin/productos/${ID}`);
    await fillAndAdjust('Agregar unidades', '3', '  ingreso de bodega  ');
    expect(await screen.findByText(/Ajuste aplicado: agregar 3 unidades \(5 → 8 disponibles\)/)).toBeTruthy();
    expect(adjustCalls()[0]!.body).toEqual({ delta: 3, reason: 'ingreso de bodega' });
    expect(getPendingAdjustment(1, ID)).toBeNull();
    await waitFor(() => expect(api.callsTo(`GET /api/inventory/${ID}`)).toHaveLength(2));
    expect(api.callsTo(`GET /api/inventory/${ID}/adjustments`)).toHaveLength(2);
  });

  it('timeout: retried with the SAME key and body; the 200 answer is not counted twice', async () => {
    serveProductPage();
    api.once('POST', `/api/inventory/${ID}/adjustments`, networkError);
    api.on('POST', `/api/inventory/${ID}/adjustments`, () => json(200, adjustment(-2, 5)));
    renderAt(`/admin/productos/${ID}`);
    await fillAndAdjust('Quitar unidades', '2', 'merma');
    expect(await screen.findByText(/El ajuste pudo haberse aplicado/)).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Aplicar ajuste' })).toBeNull(); // no second adjustment meanwhile
    const pending = getPendingAdjustment(1, ID)!;
    expect(pending).toMatchObject({ delta: -2, reason: 'merma' });
    fireEvent.click(screen.getByRole('button', { name: 'Reintentar con la misma clave' }));
    expect(await screen.findByText(/Este ajuste ya estaba aplicado \(5 → 3 disponibles\); no se sumó de nuevo/)).toBeTruthy();
    expect(adjustCalls().map(keyOf)).toEqual([pending.key, pending.key]);
    expect(adjustCalls().map((c) => c.body)).toEqual([{ delta: -2, reason: 'merma' }, { delta: -2, reason: 'merma' }]);
    expect(getPendingAdjustment(1, ID)).toBeNull();
  });

  it('reload with a pending adjustment: shown, never sent by itself, retried with the stored key and body', async () => {
    savePendingAdjustment({ userId: 1, productId: ID, delta: 4, reason: 'conteo', key: 'stored-key-1' });
    serveProductPage();
    api.on('POST', `/api/inventory/${ID}/adjustments`, () => json(201, adjustment(4, 5)));
    renderAt(`/admin/productos/${ID}`);
    expect(await screen.findByText('Ajuste sin confirmar')).toBeTruthy();
    expect(screen.getByText(/Agregar 4 unidades · motivo «conteo»/)).toBeTruthy();
    await new Promise((r) => setTimeout(r, 30));
    expect(adjustCalls()).toHaveLength(0);
    expect(screen.queryByRole('button', { name: 'Aplicar ajuste' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Reintentar con la misma clave' }));
    expect(await screen.findByText(/Ajuste aplicado/)).toBeTruthy();
    expect(keyOf(adjustCalls()[0]!)).toBe('stored-key-1');
    expect(adjustCalls()[0]!.body).toEqual({ delta: 4, reason: 'conteo' });
  });

  it('another admin never sees or sends the pending adjustment of the first one', async () => {
    savePendingAdjustment({ userId: 1, productId: ID, delta: 4, reason: '', key: 'admin1-key' });
    signIn(api, otherAdmin);
    serveProductPage();
    api.on('POST', `/api/inventory/${ID}/adjustments`, () => json(201, adjustment(1, 5)));
    renderAt(`/admin/productos/${ID}`);
    await screen.findByRole('button', { name: 'Aplicar ajuste' });
    expect(screen.queryByText('Ajuste sin confirmar')).toBeNull();
    await fillAndAdjust('Agregar unidades', '1');
    await screen.findByText(/Ajuste aplicado/);
    expect(keyOf(adjustCalls()[0]!)).not.toBe('admin1-key');
    expect(getPendingAdjustment(1, ID)?.key).toBe('admin1-key');
  });

  it('503 of inventory-service (product-service down) is definitive; 503 or 502 of anything else is not', async () => {
    serveProductPage();
    api.once('POST', `/api/inventory/${ID}/adjustments`, () => json(503, { detail: INVENTORY_PRODUCT_SERVICE_DOWN }));
    renderAt(`/admin/productos/${ID}`);
    await fillAndAdjust('Agregar unidades', '2');
    expect(await screen.findByText(/No se aplicó: inventory-service no pudo comprobar el producto/)).toBeTruthy();
    expect(getPendingAdjustment(1, ID)).toBeNull();
    expect(screen.getByRole('button', { name: 'Aplicar ajuste' })).toBeTruthy();

    api.once('POST', `/api/inventory/${ID}/adjustments`, () => json(503, { detail: 'Service Unavailable' }));
    fireEvent.click(screen.getByRole('button', { name: 'Aplicar ajuste' }));
    expect(await screen.findByText(/El servicio no confirmó el resultado/)).toBeTruthy();
    expect(getPendingAdjustment(1, ID)).not.toBeNull();

    api.once('POST', `/api/inventory/${ID}/adjustments`, gateway502);
    fireEvent.click(screen.getByRole('button', { name: 'Reintentar con la misma clave' }));
    await waitFor(() => expect(adjustCalls()).toHaveLength(3));
    expect(await screen.findByText(/El servicio no confirmó el resultado/)).toBeTruthy();
    expect(getPendingAdjustment(1, ID)).not.toBeNull();
    expect(keyOf(adjustCalls()[1]!)).toBe(keyOf(adjustCalls()[2]!));
  });

  it('409 without enough units: not applied; 409 key reused: kept until the admin discards it after a confirmation', async () => {
    serveProductPage();
    api.once('POST', `/api/inventory/${ID}/adjustments`, () => json(409, { detail: NOT_ENOUGH_UNITS }));
    renderAt(`/admin/productos/${ID}`);
    await fillAndAdjust('Quitar unidades', '50');
    expect(await screen.findByText(/No se aplicó: no hay tantas unidades disponibles/)).toBeTruthy();
    expect(getPendingAdjustment(1, ID)).toBeNull();

    api.once('POST', `/api/inventory/${ID}/adjustments`, () => json(409, { detail: KEY_REUSED }));
    fireEvent.change(screen.getByLabelText('Unidades'), { target: { value: '1' } });
    fireEvent.click(screen.getByRole('button', { name: 'Aplicar ajuste' }));
    expect(await screen.findByText(/La clave de este ajuste ya se usó con otros datos/)).toBeTruthy();
    expect(getPendingAdjustment(1, ID)).not.toBeNull();
    expect(screen.queryByRole('button', { name: 'Reintentar con la misma clave' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Descartar este intento…' }));
    expect(getPendingAdjustment(1, ID)).not.toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Sí, descartar' }));
    expect(getPendingAdjustment(1, ID)).toBeNull();
    expect(screen.getByRole('button', { name: 'Aplicar ajuste' })).toBeTruthy();
  });

  it('a double click sends one adjustment', async () => {
    serveProductPage();
    const slow = deferred();
    api.on('POST', `/api/inventory/${ID}/adjustments`, () => slow.promise);
    renderAt(`/admin/productos/${ID}`);
    fireEvent.click(await screen.findByRole('radio', { name: 'Agregar unidades' }));
    fireEvent.change(screen.getByLabelText('Unidades'), { target: { value: '1' } });
    const button = screen.getByRole('button', { name: 'Aplicar ajuste' });
    fireEvent.click(button);
    fireEvent.click(button);
    await act(async () => {
      slow.resolve(json(201, adjustment(1, 5)));
    });
    await screen.findByText(/Ajuste aplicado/);
    expect(adjustCalls()).toHaveLength(1);
  });

  it('invalid units are not sent; the history is paged 20 at a time', async () => {
    serveProductPage();
    api.on('GET', `/api/inventory/${ID}/adjustments`, ({ url }) =>
      json(200, Array.from({ length: url.searchParams.get('offset') === '0' ? 20 : 3 }, (_, i) => adjustment(1, i, i + 1))));
    renderAt(`/admin/productos/${ID}`);
    for (const value of ['0', '100001', '1.5', '-3', 'abc']) {
      await fillAndAdjust('Agregar unidades', value);
      expect(await screen.findByText(/Ingresa un número entero de unidades/)).toBeTruthy();
    }
    expect(adjustCalls()).toHaveLength(0);
    fireEvent.click(await screen.findByRole('button', { name: 'Siguiente' }));
    await waitFor(() => expect(api.callsTo(`GET /api/inventory/${ID}/adjustments`).at(-1)!.url.searchParams.get('offset')).toBe('20'));
    const call = api.callsTo(`GET /api/inventory/${ID}/adjustments`).at(-1)!;
    expect(call.url.searchParams.get('limit')).toBe('20');
  });
});
