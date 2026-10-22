// @vitest-environment jsdom
// Catalogue and product page with the real routes, session and cart providers; fetch
// answers like product-service, inventory-service and order-service behind the gateway.
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AppRoutes } from '../App';
import type { ProductOut } from '../api/types';
import { AuthProvider } from '../auth/AuthContext';
import { createFakeApi, deferred, hang, json, type FakeApi } from '../test/fakeApi';
import { admin, cart, customer, page, product, signIn, stock } from '../test/shop';
import { makeToken } from '../test/tokens';

const CATALOG: ProductOut[] = [
  product(1, { name: 'Laptop 14 pulgadas', category: 'laptops', price: '749.99' }),
  product(2, { name: 'Laptop 15 pulgadas', category: 'laptops', price: '899.00' }),
  product(3, { name: 'Monitor 24 pulgadas', category: 'monitores', price: '159.90' }),
  product(4, { name: 'Audífonos inalámbricos', category: 'audio', price: '89.00' }),
];
const STOCK = { 1: 15, 2: 0, 4: 25 }; // product 3 has no stock record

let api: FakeApi;

/** A small product-service: filters by q and category, pages with limit/offset */
function serveCatalog(products: ProductOut[]) {
  api.on('GET', '/api/products', ({ url }) => {
    const q = url.searchParams.get('q')?.toLowerCase();
    const category = url.searchParams.get('category');
    const limit = Number(url.searchParams.get('limit'));
    const offset = Number(url.searchParams.get('offset'));
    const matching = products.filter(
      (p) => p.is_active && (!q || p.name.toLowerCase().includes(q)) && (!category || p.category === category),
    );
    return json(200, page(matching.slice(offset, offset + limit), matching.length, limit, offset));
  });
  products.forEach((p) => api.on('GET', `/api/products/${p.id}`, () => json(200, p)));
}

function serveStock(rows: Record<number, number>) {
  api.on('GET', '/api/inventory', ({ url }) => {
    const ids = (url.searchParams.get('ids') ?? '').split(',').map(Number);
    return json(200, stock(rows).filter((row) => ids.includes(row.product_id)));
  });
}

function Where() {
  const location = useLocation();
  return <output data-testid="where">{`${location.pathname}${location.search}`}</output>;
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
const productCalls = () => api.callsTo('GET /api/products').filter((c) => c.url.searchParams.get('limit') !== '100');
const putCalls = (id: number) => api.callsTo(`PUT /api/cart/items/${id}`);
const card = (name: string) => screen.getByRole('link', { name }).closest('article')!;

beforeEach(() => {
  api = createFakeApi();
  vi.stubGlobal('fetch', api.fetchMock);
  sessionStorage.clear();
  serveCatalog(CATALOG);
  serveStock(STOCK);
  api.on('GET', '/api/cart', () => json(200, cart(0)));
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('catalogue', () => {
  it('lists the active products with price and the three kinds of availability', async () => {
    renderAt('/productos');
    expect(await screen.findByRole('link', { name: 'Laptop 14 pulgadas' })).toBeTruthy();
    expect(await within(card('Laptop 14 pulgadas')).findByText('15 unidades')).toBeTruthy();
    expect(within(card('Laptop 14 pulgadas')).getByText('$749,99')).toBeTruthy();
    expect(within(card('Laptop 15 pulgadas')).getByText('Agotado')).toBeTruthy();
    expect(within(card('Monitor 24 pulgadas')).getByText('Sin existencias registradas')).toBeTruthy();
    expect(screen.getByText('4 productos')).toBeTruthy();

    const [first] = productCalls();
    expect(first!.url.searchParams.get('limit')).toBe('12');
    expect(first!.url.searchParams.get('offset')).toBe('0');
    expect(first!.url.searchParams.has('include_inactive')).toBe(false);
    // One inventory request for the whole page
    expect(api.callsTo('GET /api/inventory')).toHaveLength(1);
    expect(api.callsTo('GET /api/inventory')[0]!.url.searchParams.get('ids')).toBe('1,2,3,4');
  });

  it('searches by name; the search is kept in the URL', async () => {
    renderAt('/productos');
    await screen.findByRole('link', { name: 'Monitor 24 pulgadas' });
    fireEvent.change(screen.getByLabelText('Buscar por nombre'), { target: { value: '  laptop ' } });
    fireEvent.click(screen.getByRole('button', { name: 'Buscar' }));
    expect(await screen.findByText('2 productos para «laptop»')).toBeTruthy();
    expect(screen.queryByRole('link', { name: 'Monitor 24 pulgadas' })).toBeNull();
    expect(where()).toBe('/productos?q=laptop');
    expect(productCalls().at(-1)!.url.searchParams.get('q')).toBe('laptop');
  });

  it('filters by a category taken from the active products', async () => {
    renderAt('/productos');
    const select = (await screen.findByLabelText('Categoría')) as HTMLSelectElement;
    await waitFor(() => expect(select.disabled).toBe(false));
    expect([...select.options].map((o) => o.textContent)).toEqual(['Todas las categorías', 'Audio', 'Laptops', 'Monitores']);
    fireEvent.change(select, { target: { value: 'audio' } });
    expect(await screen.findByText('1 producto en Audio')).toBeTruthy();
    expect(where()).toBe('/productos?categoria=audio');
    expect(productCalls().at(-1)!.url.searchParams.get('category')).toBe('audio');
  });

  it('pages of 12 products', async () => {
    serveCatalog(Array.from({ length: 30 }, (_, i) => product(i + 1)));
    renderAt('/productos');
    expect(await screen.findByText('Página 1 de 3')).toBeTruthy();
    fireEvent.click(screen.getByRole('link', { name: 'Siguiente' }));
    expect(await screen.findByText('Página 2 de 3')).toBeTruthy();
    expect(productCalls().at(-1)!.url.searchParams.get('offset')).toBe('12');
    expect(where()).toBe('/productos?pagina=2');
  });

  it('an invalid page in the URL becomes page 1; a page past the end offers the first one', async () => {
    renderAt('/productos?pagina=abc');
    await screen.findByRole('link', { name: 'Laptop 14 pulgadas' });
    expect(productCalls()[0]!.url.searchParams.get('offset')).toBe('0');
    cleanup();

    renderAt('/productos?pagina=9');
    expect(await screen.findByText('Esta página no existe')).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Ir a la primera página' }).getAttribute('href')).toBe('/productos');
  });

  it('no results: says so and offers to clear the filters', async () => {
    renderAt('/productos?q=zzz');
    expect(await screen.findByText('No encontramos productos con esos filtros')).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Limpiar filtros' }).getAttribute('href')).toBe('/productos');
  });

  it('product-service unavailable: error with retry, no invented products', async () => {
    api.on('GET', '/api/products', () => json(503, { detail: { code: 'UPSTREAM_UNAVAILABLE', message: 'x' } }));
    renderAt('/productos');
    expect(await screen.findByText('No pudimos cargar el catálogo.')).toBeTruthy();
    expect(screen.queryByRole('article')).toBeNull();
    expect(screen.queryByLabelText('Categoría')).toBeNull(); // categories could not be read either

    serveCatalog(CATALOG);
    fireEvent.click(screen.getByRole('button', { name: 'Reintentar' }));
    expect(await screen.findByRole('link', { name: 'Laptop 14 pulgadas' })).toBeTruthy();
  });

  it('inventory-service unavailable: products are shown, availability is marked as not verified', async () => {
    api.on('GET', '/api/inventory', () => json(502, { detail: { code: 'UPSTREAM_UNAVAILABLE', message: 'x' } }));
    renderAt('/productos');
    expect(await screen.findByText(/No pudimos consultar la disponibilidad/)).toBeTruthy();
    expect(screen.getAllByText('Disponibilidad no verificada')).toHaveLength(4);
    expect(screen.queryByText('Agotado')).toBeNull();
  });

  it('more than 500 products: categories come from 5 pages of 100 and the page says the list may be incomplete', async () => {
    api.on('GET', '/api/products', ({ url }) => {
      const limit = Number(url.searchParams.get('limit'));
      const offset = Number(url.searchParams.get('offset'));
      const items = Array.from({ length: limit }, (_, i) => product(offset + i + 1, { category: `cat-${(offset + i) % 9}` }));
      return json(200, page(items, 650, limit, offset));
    });
    renderAt('/productos');
    expect(await screen.findByText('Categorías tomadas de los primeros 500 de 650 productos.')).toBeTruthy();
    const scans = api.callsTo('GET /api/products').filter((c) => c.url.searchParams.get('limit') === '100');
    expect(scans.map((c) => c.url.searchParams.get('offset'))).toEqual(['0', '100', '200', '300', '400']);
    expect(screen.getByText('Página 1 de 55')).toBeTruthy();
  });
});

describe('product page', () => {
  it('anonymous: asks to sign in and comes back to the product afterwards', async () => {
    const token = makeToken({ sub: '7', role: 'customer' });
    api.on('POST', '/api/auth/login', () => json(200, { access_token: token, token_type: 'bearer', expires_in: 1800 }));
    api.on('GET', '/api/users/me', () => json(200, customer));
    renderAt('/productos/1');
    fireEvent.click(await screen.findByRole('link', { name: 'Inicia sesión para comprar' }));
    expect(await screen.findByRole('heading', { name: 'Iniciar sesión' })).toBeTruthy();
    fireEvent.change(screen.getByLabelText('Correo'), { target: { value: 'ana@example.com' } });
    fireEvent.change(screen.getByLabelText('Contraseña'), { target: { value: 'secret-pass' } });
    fireEvent.click(screen.getByRole('button', { name: 'Iniciar sesión' }));
    expect(await screen.findByRole('button', { name: 'Agregar al carrito' })).toBeTruthy();
    expect(where()).toBe('/productos/1');
  });

  it('an invalid id shows "not found" without calling product-service', async () => {
    renderAt('/productos/abc');
    expect(await screen.findByRole('heading', { name: 'No encontramos esta página' })).toBeTruthy();
    expect(api.calls.some((c) => c.key.startsWith('GET /api/products/'))).toBe(false);
  });

  it('404 from product-service: product not found', async () => {
    renderAt('/productos/99');
    expect(await screen.findByRole('heading', { name: 'Producto no encontrado' })).toBeTruthy();
  });

  it('sold out and unregistered products cannot be added', async () => {
    signIn(api);
    renderAt('/productos/2');
    expect(await screen.findByText('Este producto está agotado, por eso no se puede agregar al carrito.')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Agregar al carrito' })).toBeNull();
    cleanup();

    renderAt('/productos/3');
    expect(await screen.findByText(/no tiene existencias registradas/)).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Agregar al carrito' })).toBeNull();
    expect(putCalls(2)).toHaveLength(0);
    expect(putCalls(3)).toHaveLength(0);
  });

  it('an inactive product (visible to an admin) cannot be added to the cart', async () => {
    const token = signIn(api, admin);
    // Like product-service: inactive products are only found with an admin token
    api.on('GET', '/api/products/5', ({ init }) =>
      (init?.headers as Record<string, string>).Authorization === `Bearer ${token}`
        ? json(200, product(5, { is_active: false }))
        : json(404, { detail: 'Product not found' }),
    );
    // Opened directly (reload or link): the product must be requested after the session is restored
    renderAt('/productos/5');
    expect(await screen.findByText('Este producto está inactivo y no se puede agregar al carrito.')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Agregar al carrito' })).toBeNull();
  });

  it('an admin account can use the cart, as order-service allows', async () => {
    signIn(api, admin);
    api.on('PUT', '/api/cart/items/1', () => json(200, cart(1, [{ id: 1, quantity: 1, price: '749.99' }])));
    renderAt('/productos/1');
    await screen.findByText('15 unidades');
    fireEvent.click(await screen.findByRole('button', { name: 'Agregar al carrito' }));
    expect(await screen.findByText('Producto agregado. Tienes 1 en tu carrito.')).toBeTruthy();
    expect(putCalls(1)[0]!.body).toEqual({ quantity: 1, expected_version: 0 });
    expect(screen.getByRole('link', { name: 'Carrito, 1 unidad' })).toBeTruthy();
  });

  it('adds to what the cart already has: PUT sends the absolute total and the last version', async () => {
    signIn(api);
    api.on('GET', '/api/cart', () => json(200, cart(4, [{ id: 1, quantity: 2, price: '749.99' }])));
    api.on('PUT', '/api/cart/items/1', () => json(200, cart(5, [{ id: 1, quantity: 5, price: '749.99' }])));
    renderAt('/productos/1');
    expect(await screen.findByText('Ya tienes 2 en tu carrito.')).toBeTruthy();
    await screen.findByText('15 unidades');
    fireEvent.change(screen.getByLabelText('Cantidad'), { target: { value: '3' } });
    fireEvent.click(screen.getByRole('button', { name: 'Agregar al carrito' }));
    expect(await screen.findByText('Producto agregado. Tienes 5 en tu carrito.')).toBeTruthy();
    expect(putCalls(1).map((c) => c.body)).toEqual([{ quantity: 5, expected_version: 4 }]);
    expect(screen.getByRole('link', { name: 'Carrito, 5 unidades' })).toBeTruthy();
  });

  it('invalid quantities are refused before sending anything', async () => {
    signIn(api);
    api.on('GET', '/api/cart', () => json(200, cart(4, [{ id: 1, quantity: 13, price: '749.99' }])));
    renderAt('/productos/1');
    await screen.findByText('Ya tienes 13 en tu carrito.');
    await screen.findByText('15 unidades');
    const input = screen.getByLabelText('Cantidad');
    const button = screen.getByRole('button', { name: 'Agregar al carrito' }) as HTMLButtonElement;
    for (const [value, message] of [
      ['abc', 'La cantidad debe ser un número entero.'],
      ['1.5', 'La cantidad debe ser un número entero.'],
      ['0', 'La cantidad mínima es 1.'],
      ['', 'Ingresa una cantidad.'],
      ['3', 'Puedes agregar como máximo 2.'], // 15 in stock, 13 already in the cart
    ] as const) {
      fireEvent.change(input, { target: { value } });
      expect(screen.getByText(message)).toBeTruthy();
      expect(button.disabled).toBe(true);
    }
    fireEvent.submit(button.closest('form')!);
    expect(putCalls(1)).toHaveLength(0);
  });

  it('never more than 99 units of a product, even when the stock is unknown', async () => {
    signIn(api);
    api.on('GET', '/api/inventory', () => json(503, { detail: 'x' }));
    api.on('GET', '/api/cart', () => json(200, cart(4, [{ id: 1, quantity: 98, price: '749.99' }])));
    renderAt('/productos/1');
    await screen.findByText('Ya tienes 98 en tu carrito.');
    fireEvent.change(screen.getByLabelText('Cantidad'), { target: { value: '2' } });
    expect(screen.getByText('Puedes agregar como máximo 1.')).toBeTruthy();
  });

  it('inventory-service down: says the stock cannot be verified and still lets the product be added', async () => {
    signIn(api);
    api.on('GET', '/api/inventory', () => json(502, { detail: { code: 'UPSTREAM_UNAVAILABLE', message: 'x' } }));
    api.on('PUT', '/api/cart/items/1', () => json(200, cart(1, [{ id: 1, quantity: 1, price: '749.99' }])));
    renderAt('/productos/1');
    expect(await screen.findByText(/No pudimos verificar la disponibilidad/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Agregar al carrito' }));
    expect(await screen.findByText('Producto agregado. Tienes 1 en tu carrito.')).toBeTruthy();
  });

  it('the price changed since the page was opened: the user is told and the product is read again', async () => {
    signIn(api);
    api.on('PUT', '/api/cart/items/1', () => json(200, cart(1, [{ id: 1, quantity: 1, price: '799.00' }])));
    renderAt('/productos/1');
    await screen.findByText('15 unidades');
    expect(api.callsTo('GET /api/products/1')).toHaveLength(1);
    fireEvent.click(screen.getByRole('button', { name: 'Agregar al carrito' }));
    expect(await screen.findByText(/El precio de este producto cambió a \$799,00/)).toBeTruthy();
    await waitFor(() => expect(api.callsTo('GET /api/products/1')).toHaveLength(2));
  });

  it('the internal lock sends one PUT even if the form is submitted twice', async () => {
    signIn(api);
    const answer = deferred();
    api.on('PUT', '/api/cart/items/1', () => answer.promise);
    renderAt('/productos/1');
    await screen.findByText('15 unidades');
    const form = screen.getByRole('button', { name: 'Agregar al carrito' }).closest('form')!;
    fireEvent.submit(form);
    fireEvent.submit(form); // bypasses the disabled button on purpose
    await waitFor(() => expect(putCalls(1)).toHaveLength(1));
    answer.resolve(json(200, cart(1, [{ id: 1, quantity: 1, price: '749.99' }])));
    expect(await screen.findByText('Producto agregado. Tienes 1 en tu carrito.')).toBeTruthy();
    expect(putCalls(1)).toHaveLength(1);
  });

  it('CART_FULL and PRODUCT_NOT_AVAILABLE', async () => {
    signIn(api);
    api.once('PUT', '/api/cart/items/1', () => json(409, { detail: { code: 'CART_FULL', message: 'x' } }));
    api.on('PUT', '/api/cart/items/1', () => json(404, { detail: { code: 'PRODUCT_NOT_AVAILABLE', message: 'x' } }));
    renderAt('/productos/1');
    await screen.findByText('15 unidades');
    fireEvent.click(screen.getByRole('button', { name: 'Agregar al carrito' }));
    expect(await screen.findByText(/ya tiene 50 productos distintos/)).toBeTruthy();

    // Deactivated meanwhile: the product is read again and is no longer found
    api.on('GET', '/api/products/1', () => json(404, { detail: 'Product not found' }));
    fireEvent.click(screen.getByRole('button', { name: 'Agregar al carrito' }));
    expect(await screen.findByRole('heading', { name: 'Producto no encontrado' })).toBeTruthy();
  });

  it('503 from order-service (catalogue not answering): the cart did not change and is not read again', async () => {
    signIn(api);
    api.on('PUT', '/api/cart/items/1', () => json(503, { detail: 'product-service is not available, try again later' }));
    renderAt('/productos/1');
    await screen.findByText('15 unidades');
    await waitFor(() => expect(api.callsTo('GET /api/cart')).toHaveLength(1));
    fireEvent.click(screen.getByRole('button', { name: 'Agregar al carrito' }));
    expect(await screen.findByText(/el catálogo no responde. Tu carrito no cambió./)).toBeTruthy();
    expect(api.callsTo('GET /api/cart')).toHaveLength(1);
    expect((screen.getByRole('button', { name: 'Agregar al carrito' }) as HTMLButtonElement).disabled).toBe(false);
  });

  it('timeout after a PUT that may have been applied: the cart is read again before another change is allowed', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    signIn(api);
    const reread = deferred();
    api.once('GET', '/api/cart', () => json(200, cart(4)));
    api.on('GET', '/api/cart', () => reread.promise);
    api.on('PUT', '/api/cart/items/1', hang); // no answer: the client gives up after 15 s
    renderAt('/productos/1');
    await screen.findByText('15 unidades');
    const button = screen.getByRole('button', { name: 'Agregar al carrito' }) as HTMLButtonElement;
    fireEvent.click(button);
    await waitFor(() => expect(putCalls(1)).toHaveLength(1));

    await act(async () => {
      await vi.advanceTimersByTimeAsync(15_000);
    });
    // The outcome is unknown: GET /cart was sent and nothing can be changed until it answers
    await waitFor(() => expect(api.callsTo('GET /api/cart')).toHaveLength(2));
    expect(await screen.findByText('Consultando tu carrito…')).toBeTruthy();
    fireEvent.submit(button.closest('form')!);
    expect(putCalls(1)).toHaveLength(1);

    // order-service had applied it
    reread.resolve(json(200, cart(5, [{ id: 1, quantity: 1, price: '749.99' }])));
    expect(await screen.findByText(/volvimos a consultar tu carrito/)).toBeTruthy();
    expect(await screen.findByText('Ya tienes 1 en tu carrito.')).toBeTruthy();
    expect(button.disabled).toBe(false);
    expect(putCalls(1)).toHaveLength(1); // never repeated automatically
  });
});
