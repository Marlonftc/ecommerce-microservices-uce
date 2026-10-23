// @vitest-environment jsdom
// Cart page and CartContext with the real routes and session; fetch answers like
// order-service behind the gateway. Covers versions, conflicts, uncertain outcomes,
// old answers and a change of user while a request is running.
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AppRoutes } from '../App';
import { AuthProvider } from '../auth/AuthContext';
import { createFakeApi, deferred, json, type FakeApi } from '../test/fakeApi';
import { cart, customer, otherCustomer, signIn, unpricedCart } from '../test/shop';
import { makeToken } from '../test/tokens';

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

const TWO_LINES = [
  { id: 1, quantity: 2, price: '749.99', name: 'Laptop 14 pulgadas' },
  { id: 2, quantity: 1, price: '19.50', name: 'Mouse inalámbrico' },
];

const line = (name: string) => screen.getByRole('link', { name }).closest('li')!;
const button = (name: string) => screen.getByRole('button', { name }) as HTMLButtonElement;
const conflict = (code: string) => json(409, { detail: { code, message: 'x' } });

beforeEach(() => {
  api = createFakeApi();
  vi.stubGlobal('fetch', api.fetchMock);
  sessionStorage.clear();
  signIn(api);
  api.on('GET', '/api/cart', () => json(200, cart(5, TWO_LINES)));
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('reading the cart', () => {
  it('shows each line and the amounts calculated by order-service, without adding them up again', async () => {
    // estimated_total deliberately differs from the sum of the subtotals: the page must show the backend value
    api.on('GET', '/api/cart', () => json(200, { ...cart(5, TWO_LINES), estimated_total: '1519.48' }));
    renderAt('/carrito');
    await screen.findByRole('link', { name: 'Laptop 14 pulgadas' });
    expect(within(line('Laptop 14 pulgadas')).getByText('$749,99')).toBeTruthy();
    expect(within(line('Laptop 14 pulgadas')).getByText('$1.499,98')).toBeTruthy();
    expect(screen.getByText('$1.519,48')).toBeTruthy();
    expect(screen.getByText('3 unidades de 2 productos')).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Carrito, 3 unidades' })).toBeTruthy();
    expect(screen.getByText(/El precio final se confirma al crear el pedido/)).toBeTruthy();
  });

  it('an empty cart offers the catalogue', async () => {
    api.on('GET', '/api/cart', () => json(200, cart(0)));
    renderAt('/carrito');
    expect(await screen.findByText('Tu carrito está vacío')).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Ver catálogo' }).getAttribute('href')).toBe('/productos');
  });

  it('without a session the cart page sends to login', async () => {
    sessionStorage.clear();
    renderAt('/carrito');
    expect(await screen.findByRole('heading', { name: 'Iniciar sesión' })).toBeTruthy();
    expect(api.callsTo('GET /api/cart')).toHaveLength(0);
  });

  it('order-service unavailable: error and retry', async () => {
    api.on('GET', '/api/cart', () => json(502, { detail: { code: 'UPSTREAM_UNAVAILABLE', message: 'x' } }));
    renderAt('/carrito');
    expect(await screen.findByText('No pudimos cargar tu carrito.')).toBeTruthy();
    api.on('GET', '/api/cart', () => json(200, cart(5, TWO_LINES)));
    fireEvent.click(button('Reintentar'));
    expect(await screen.findByRole('link', { name: 'Laptop 14 pulgadas' })).toBeTruthy();
  });

  it('product-service down: products and quantities are kept, without prices; they can still be removed', async () => {
    api.on('GET', '/api/cart', () => json(200, unpricedCart(5, [{ id: 3, quantity: 4 }])));
    api.on('DELETE', '/api/cart/items/3', () => json(200, unpricedCart(6, [])));
    renderAt('/carrito');
    expect(await screen.findByRole('link', { name: 'Producto #3' })).toBeTruthy();
    expect(screen.getByText('Precios no disponibles')).toBeTruthy();
    expect(screen.getByText('No disponible por ahora')).toBeTruthy();
    expect(within(line('Producto #3')).getByText('4')).toBeTruthy();
    // No old price is shown as current, and the quantity cannot be changed (PUT needs product-service)
    expect(within(line('Producto #3')).queryByText(/\$/)).toBeNull();
    expect(within(line('Producto #3')).queryByRole('textbox')).toBeNull();

    fireEvent.click(button('Eliminar Producto #3'));
    expect(await screen.findByText('Tu carrito está vacío')).toBeTruthy();
    expect(api.callsTo('DELETE /api/cart/items/3')[0]!.url.searchParams.get('expected_version')).toBe('5');
  });

  it('a product deactivated after it was added is marked, not counted and can be removed', async () => {
    api.on('GET', '/api/cart', () =>
      json(200, cart(5, [{ id: 1, quantity: 1, price: '749.99', name: 'Laptop 14 pulgadas' }, { id: 9, quantity: 2, available: false }])),
    );
    renderAt('/carrito');
    expect(await screen.findByText('Ya no está disponible')).toBeTruthy();
    expect(screen.getByText('Los productos que ya no están disponibles no se incluyen en el total.')).toBeTruthy();
    expect(within(line('Producto #9')).queryByRole('textbox')).toBeNull();
    expect(button('Eliminar Producto #9').disabled).toBe(false);
  });

  it('a change made in another tab is shown when this tab becomes visible again', async () => {
    renderAt('/carrito');
    await screen.findByRole('link', { name: 'Mouse inalámbrico' });
    api.on('GET', '/api/cart', () => json(200, cart(6, [TWO_LINES[0]!])));
    act(() => {
      document.dispatchEvent(new Event('visibilitychange'));
    });
    await waitFor(() => expect(screen.queryByRole('link', { name: 'Mouse inalámbrico' })).toBeNull());
  });

  it('401 while reading the cart ends the session (AuthContext)', async () => {
    api.on('GET', '/api/cart', () => json(401, { detail: 'Invalid or expired token' }));
    renderAt('/carrito');
    expect(await screen.findByText('Tu sesión ya no es válida. Inicia sesión de nuevo.')).toBeTruthy();
  });
});

describe('changing the cart', () => {
  it('updates a quantity with PUT, the absolute value and the last version', async () => {
    api.on('PUT', '/api/cart/items/2', () => json(200, cart(6, [TWO_LINES[0]!, { ...TWO_LINES[1]!, quantity: 4 }])));
    renderAt('/carrito');
    await screen.findByRole('link', { name: 'Mouse inalámbrico' });
    fireEvent.change(screen.getByLabelText('Cantidad de Mouse inalámbrico'), { target: { value: '4' } });
    fireEvent.click(within(line('Mouse inalámbrico')).getByRole('button', { name: 'Actualizar' }));
    expect(await screen.findByText('Cantidad actualizada.')).toBeTruthy();
    expect(api.callsTo('PUT /api/cart/items/2').map((c) => c.body)).toEqual([{ quantity: 4, expected_version: 5 }]);
    expect(screen.getByRole('link', { name: 'Carrito, 6 unidades' })).toBeTruthy();
  });

  it('invalid quantities in a line are not sent', async () => {
    renderAt('/carrito');
    await screen.findByRole('link', { name: 'Mouse inalámbrico' });
    const input = screen.getByLabelText('Cantidad de Mouse inalámbrico');
    for (const value of ['0', '100', 'abc', '2.5']) {
      fireEvent.change(input, { target: { value } });
      expect(within(line('Mouse inalámbrico')).queryByRole('button', { name: 'Actualizar' })).toBeNull();
    }
    expect(api.callsTo('PUT /api/cart/items/2')).toHaveLength(0);
  });

  it('removes a product and empties the cart, after confirmation', async () => {
    api.on('DELETE', '/api/cart/items/2', () => json(200, cart(6, [TWO_LINES[0]!])));
    api.on('DELETE', '/api/cart', () => json(200, cart(7)));
    renderAt('/carrito');
    await screen.findByRole('link', { name: 'Mouse inalámbrico' });
    fireEvent.click(button('Eliminar Mouse inalámbrico'));
    expect(await screen.findByText('Producto eliminado del carrito.')).toBeTruthy();
    expect(api.callsTo('DELETE /api/cart/items/2')[0]!.url.searchParams.get('expected_version')).toBe('5');

    fireEvent.click(button('Vaciar carrito'));
    fireEvent.click(button('Cancelar'));
    expect(api.callsTo('DELETE /api/cart')).toHaveLength(0);
    fireEvent.click(button('Vaciar carrito'));
    fireEvent.click(button('Sí, vaciar'));
    expect(await screen.findByText('Tu carrito está vacío')).toBeTruthy();
    expect(api.callsTo('DELETE /api/cart')[0]!.url.searchParams.get('expected_version')).toBe('6');
  });

  it('CART_CHANGED: the cart is read again, the user is told, and the change is NOT repeated', async () => {
    api.on('PUT', '/api/cart/items/2', () => conflict('CART_CHANGED'));
    renderAt('/carrito');
    await screen.findByRole('link', { name: 'Mouse inalámbrico' });
    await waitFor(() => expect(button('Vaciar carrito').disabled).toBe(false));
    const readsBefore = api.callsTo('GET /api/cart').length;
    // Another tab changed the cart in the meantime
    api.on('GET', '/api/cart', () => json(200, cart(7, [TWO_LINES[0]!, { ...TWO_LINES[1]!, quantity: 9 }])));
    fireEvent.change(screen.getByLabelText('Cantidad de Mouse inalámbrico'), { target: { value: '3' } });
    fireEvent.click(within(line('Mouse inalámbrico')).getByRole('button', { name: 'Actualizar' }));
    expect(await screen.findByText(/Tu carrito cambió en otra pestaña o dispositivo/)).toBeTruthy();
    await waitFor(() => expect((screen.getByLabelText('Cantidad de Mouse inalámbrico') as HTMLInputElement).value).toBe('9'));
    expect(api.callsTo('GET /api/cart').length).toBe(readsBefore + 1);
    expect(api.callsTo('PUT /api/cart/items/2')).toHaveLength(1);
  });

  it('CART_LOCKED: the cart is read again, shown as locked, and the change is NOT repeated', async () => {
    api.on('DELETE', '/api/cart/items/1', () => conflict('CART_LOCKED'));
    renderAt('/carrito');
    await screen.findByRole('link', { name: 'Laptop 14 pulgadas' });
    api.on('GET', '/api/cart', () => json(200, { ...cart(5, TWO_LINES), locked: true }));
    fireEvent.click(button('Eliminar Laptop 14 pulgadas'));
    expect(await screen.findByText('Compra en curso')).toBeTruthy();
    expect(button('Eliminar Laptop 14 pulgadas').disabled).toBe(true);
    expect(button('Eliminar Mouse inalámbrico').disabled).toBe(true);
    expect(button('Vaciar carrito').disabled).toBe(true);
    expect(api.callsTo('DELETE /api/cart/items/1')).toHaveLength(1);
  });

  it('a cart that arrives locked (checkout in progress) cannot be changed', async () => {
    api.on('GET', '/api/cart', () => json(200, { ...cart(5, TWO_LINES), locked: true }));
    renderAt('/carrito');
    expect(await screen.findByText('Hay una compra en curso. El carrito no se puede modificar hasta que termine.')).toBeTruthy();
    expect((screen.getByLabelText('Cantidad de Mouse inalámbrico') as HTMLInputElement).disabled).toBe(true);
    expect(button('Vaciar carrito').disabled).toBe(true);
  });

  it('network error on DELETE: the cart is read again before allowing another change', async () => {
    const reread = deferred();
    renderAt('/carrito');
    await screen.findByRole('link', { name: 'Mouse inalámbrico' });
    await waitFor(() => expect(button('Vaciar carrito').disabled).toBe(false));
    api.on('DELETE', '/api/cart/items/2', () => Promise.reject(new TypeError('Failed to fetch')));
    api.on('GET', '/api/cart', () => reread.promise);
    fireEvent.click(button('Eliminar Mouse inalámbrico'));

    expect(await screen.findByText('Confirmando tu carrito')).toBeTruthy();
    expect(button('Eliminar Laptop 14 pulgadas').disabled).toBe(true);
    fireEvent.click(button('Eliminar Laptop 14 pulgadas'));
    expect(api.callsTo('DELETE /api/cart/items/1')).toHaveLength(0);

    // The DELETE had reached order-service
    reread.resolve(json(200, cart(6, [TWO_LINES[0]!])));
    expect(await screen.findByText(/volvimos a consultar tu carrito/)).toBeTruthy();
    expect(screen.queryByRole('link', { name: 'Mouse inalámbrico' })).toBeNull();
    expect(button('Eliminar Laptop 14 pulgadas').disabled).toBe(false);
    expect(api.callsTo('DELETE /api/cart/items/2')).toHaveLength(1);
  });

  it('gateway 504 and the re-read also fails: changes stay blocked until a read succeeds', async () => {
    renderAt('/carrito');
    await screen.findByRole('link', { name: 'Mouse inalámbrico' });
    await waitFor(() => expect(button('Vaciar carrito').disabled).toBe(false));
    api.on('DELETE', '/api/cart/items/2', () => json(504, { detail: { code: 'UPSTREAM_TIMEOUT', message: 'x' } }));
    api.on('GET', '/api/cart', () => json(502, { detail: { code: 'UPSTREAM_UNAVAILABLE', message: 'x' } }));
    fireEvent.click(button('Eliminar Mouse inalámbrico'));
    expect(await screen.findByText(/tampoco pudimos consultar tu carrito/)).toBeTruthy();
    expect(button('Eliminar Laptop 14 pulgadas').disabled).toBe(true);
    // The block is inside CartContext, not only in the disabled controls: a forced submit sends nothing
    api.on('PUT', '/api/cart/items/1', () => json(200, cart(6, TWO_LINES)));
    const laptopQuantity = screen.getByLabelText('Cantidad de Laptop 14 pulgadas');
    fireEvent.change(laptopQuantity, { target: { value: '3' } });
    fireEvent.submit(laptopQuantity.closest('form')!);
    await act(async () => {
      await Promise.resolve();
    });
    expect(api.callsTo('PUT /api/cart/items/1')).toHaveLength(0);

    api.on('GET', '/api/cart', () => json(200, cart(5, TWO_LINES))); // it had NOT been applied
    fireEvent.click(button('Reintentar'));
    await waitFor(() => expect(button('Eliminar Laptop 14 pulgadas').disabled).toBe(false));
    expect(screen.getByRole('link', { name: 'Mouse inalámbrico' })).toBeTruthy();
    expect(api.callsTo('DELETE /api/cart/items/2')).toHaveLength(1);
  });

  it('an old answer that arrives after a newer change does not overwrite it', async () => {
    renderAt('/carrito');
    await screen.findByRole('link', { name: 'Mouse inalámbrico' });
    await waitFor(() => expect(button('Vaciar carrito').disabled).toBe(false));

    // A read starts (tab visible again) and stays pending with the old version 5
    const slowRead = deferred();
    api.on('GET', '/api/cart', () => slowRead.promise);
    const readsBefore = api.callsTo('GET /api/cart').length;
    act(() => {
      document.dispatchEvent(new Event('visibilitychange'));
    });
    await waitFor(() => expect(api.callsTo('GET /api/cart')).toHaveLength(readsBefore + 1));

    // Meanwhile a change is confirmed with version 6
    api.on('PUT', '/api/cart/items/2', () => json(200, cart(6, [TWO_LINES[0]!, { ...TWO_LINES[1]!, quantity: 4 }])));
    fireEvent.change(screen.getByLabelText('Cantidad de Mouse inalámbrico'), { target: { value: '4' } });
    fireEvent.click(within(line('Mouse inalámbrico')).getByRole('button', { name: 'Actualizar' }));
    expect(await screen.findByText('Cantidad actualizada.')).toBeTruthy();

    slowRead.resolve(json(200, cart(5, TWO_LINES)));
    await act(async () => {
      await slowRead.promise;
    });
    expect((screen.getByLabelText('Cantidad de Mouse inalámbrico') as HTMLInputElement).value).toBe('4');
    expect(screen.getByRole('link', { name: 'Carrito, 6 unidades' })).toBeTruthy();
  });

  it('change of user while a request is running: the late answer of the old user is ignored', async () => {
    const lateAnswer = deferred();
    // The old request is NOT cancelled by the server: it answers late anyway
    api.on('PUT', '/api/cart/items/2', () => lateAnswer.promise);
    renderAt('/carrito');
    await screen.findByRole('link', { name: 'Mouse inalámbrico' });
    await waitFor(() => expect(button('Vaciar carrito').disabled).toBe(false));
    fireEvent.change(screen.getByLabelText('Cantidad de Mouse inalámbrico'), { target: { value: '7' } });
    fireEvent.click(within(line('Mouse inalámbrico')).getByRole('button', { name: 'Actualizar' }));
    await waitFor(() => expect(api.callsTo('PUT /api/cart/items/2')).toHaveLength(1));

    // Ana signs out, Luis signs in on the same tab
    fireEvent.click(button('Cerrar sesión'));
    expect(screen.queryByRole('link', { name: /^Carrito/ })).toBeNull();
    const luisToken = makeToken({ sub: '8', role: 'customer' }, 'luis');
    api.on('POST', '/api/auth/login', () => json(200, { access_token: luisToken, token_type: 'bearer', expires_in: 1800 }));
    api.on('GET', '/api/users/me', () => json(200, otherCustomer));
    api.on('GET', '/api/cart', () => json(200, cart(1, [{ id: 4, quantity: 1, price: '59.99', name: 'Teclado mecánico' }])));
    fireEvent.click(screen.getByRole('link', { name: 'Iniciar sesión' }));
    fireEvent.change(await screen.findByLabelText('Correo'), { target: { value: otherCustomer.email } });
    fireEvent.change(screen.getByLabelText('Contraseña'), { target: { value: 'luis-password' } });
    fireEvent.click(screen.getByRole('button', { name: 'Iniciar sesión' }));
    expect(await screen.findByRole('link', { name: 'Carrito, 1 unidad' })).toBeTruthy();

    // Ana's PUT answers now, with a newer version: it must not reach Luis's cart
    lateAnswer.resolve(json(200, cart(9, [TWO_LINES[0]!, { ...TWO_LINES[1]!, quantity: 7 }])));
    await act(async () => {
      await lateAnswer.promise;
    });
    fireEvent.click(screen.getByRole('link', { name: 'Carrito, 1 unidad' }));
    expect(await screen.findByRole('link', { name: 'Teclado mecánico' })).toBeTruthy();
    expect(screen.queryByRole('link', { name: 'Mouse inalámbrico' })).toBeNull();
    expect(screen.getByRole('link', { name: 'Carrito, 1 unidad' })).toBeTruthy();
    // Luis's requests carried Luis's token only
    const luisReads = api.callsTo('GET /api/cart').filter((c) => (c.init?.headers as Record<string, string>).Authorization === `Bearer ${luisToken}`);
    expect(luisReads.length).toBeGreaterThan(0);
    expect(customer.id).not.toBe(otherCustomer.id);
  });

  it('signing out forgets the cart in memory; nothing is kept in sessionStorage', async () => {
    renderAt('/carrito');
    await screen.findByRole('link', { name: 'Carrito, 3 unidades' });
    expect(Object.keys(sessionStorage)).toEqual(['ecommerce-uce.session']);
    expect(sessionStorage.getItem('ecommerce-uce.session')).not.toContain('items');
    fireEvent.click(button('Cerrar sesión'));
    expect(screen.queryByRole('link', { name: /^Carrito/ })).toBeNull();
    expect(sessionStorage.length).toBe(0);
  });
});
