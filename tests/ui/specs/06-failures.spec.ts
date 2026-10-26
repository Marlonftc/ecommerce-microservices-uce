// Failure scenarios with real containers stopped through Docker Compose. Every test
// starts the service again in "finally" and waits until it is healthy and reachable
// through the gateway; global teardown repeats that for any service left stopped.
import { test, expect, signIn } from '../support/fixtures';
import {
  adminLogin, auth, createOrder, getOrder, newCustomer, newProduct, payOrder, putInCart,
} from '../support/api';
import { withServiceStopped } from '../support/services';

// Stopping, starting and waiting for health checks takes time
test.describe.configure({ timeout: 240_000 });

test('product-service down: the catalogue shows an error and recovers with Reintentar', async ({ page }) => {
  await page.goto('/productos');
  await expect(page.getByRole('heading', { name: 'Catálogo' })).toBeVisible();
  await withServiceStopped('product-service', async () => {
    await page.reload();
    await expect(page.getByText('No pudimos cargar el catálogo.')).toBeVisible({ timeout: 45_000 });
  });
  await page.getByRole('button', { name: 'Reintentar' }).click();
  await expect(page.locator('.product-grid')).toBeVisible();
});

test('notification-service down: the bell shows "?" (never 0) and orders still work', async ({ page, api }) => {
  const customer = await newCustomer(api, 'Cliente Campana UI6');
  await signIn(page, customer.token);
  await expect(page.getByRole('link', { name: 'Notificaciones, 0 sin leer' })).toBeVisible();
  await withServiceStopped('notification-service', async () => {
    // Visiting the orders refreshes the counter
    await page.getByRole('link', { name: 'Mis pedidos' }).click();
    await expect(page.getByRole('heading', { name: 'Mis pedidos' })).toBeVisible();
    const bell = page.getByRole('link', { name: 'Notificaciones (no se pudo consultar cuántas hay sin leer)' });
    await expect(bell).toBeVisible({ timeout: 45_000 });
    await expect(bell).toHaveText('?');
  });
  await page.getByRole('link', { name: /^Notificaciones/ }).click();
  await expect(page.getByRole('heading', { level: 1, name: 'Notificaciones' })).toBeVisible();
  await expect(page.getByRole('link', { name: 'Notificaciones, 0 sin leer' })).toBeVisible({ timeout: 45_000 });
});

test('inventory-service down: checkout waits in RESERVING and completes once it is back', async ({ page, api }) => {
  const product = await newProduct(api, { label: 'Reserva', stock: 2 });
  const customer = await newCustomer(api, 'Cliente Reserva UI6');
  await putInCart(api, customer.token, product.id, 1);
  await signIn(page, customer.token, '/checkout');
  await expect(page.getByRole('button', { name: 'Confirmar pedido' })).toBeVisible();

  await withServiceStopped('inventory-service', async () => {
    await page.getByRole('button', { name: 'Confirmar pedido' }).click();
    await expect(page.getByText('Reservando tus productos')).toBeVisible({ timeout: 45_000 });
  });
  // Same Idempotency-Key: the saved attempt finishes and never creates a second order
  const retry = page.getByRole('button', { name: 'Reintentar' });
  await expect(async () => {
    if (await retry.isVisible()) await retry.click();
    await expect(page).toHaveURL(/\/pedidos\/[0-9a-f-]{36}$/, { timeout: 5_000 });
  }).toPass({ timeout: 90_000 });
  const orderId = page.url().split('/pedidos/')[1]!;
  expect((await getOrder(api, customer.token, orderId)).status).toBe('PENDING_PAYMENT');
  const orders = await api.get('/api/orders?size=10', { headers: auth(customer.token) });
  expect(((await orders.json()) as { total: number }).total).toBe(1);
});

test('inventory-service down: the admin product page reports it without blocking', async ({ page, api }) => {
  const product = await newProduct(api, { label: 'Inventario', stock: 3 });
  await signIn(page, await adminLogin(api), `/admin/productos/${product.id}`);
  await expect(page.getByRole('heading', { name: product.name })).toBeVisible();
  await withServiceStopped('inventory-service', async () => {
    await page.reload();
    await expect(page.getByText('Inventario no disponible en este momento.')).toBeVisible({ timeout: 45_000 });
    // Product data is still editable: it lives in product-service
    await expect(page.getByLabel('Nombre')).toHaveValue(product.name);
  });
});

test('user-service down: admin order list falls back to "Cliente #ID"', async ({ page, api }) => {
  const product = await newProduct(api, { label: 'Usuarios', stock: 2 });
  const customer = await newCustomer(api, 'Cliente Sin Nombre UI6');
  await createOrder(api, customer.token, product.id);
  await signIn(page, await adminLogin(api));
  await withServiceStopped('user-service', async () => {
    await page.getByRole('navigation', { name: 'Navegación administrativa' }).getByRole('link', { name: 'Pedidos', exact: true }).click();
    await expect(page.getByText(/No pudimos obtener los nombres de los clientes/)).toBeVisible({ timeout: 45_000 });
    await expect(page.getByText(`Cliente #${customer.id}`).first()).toBeVisible();
    expect(await page.locator('.order-row').count()).toBeGreaterThan(0);
  });
});

test('notification left FAILED by the worker is retried by the admin once the service is back', async ({ page, api }) => {
  // 10 fast attempts of order-worker (backoff up to 10 s in compose.e2e.yaml) before FAILED
  test.setTimeout(420_000);
  const product = await newProduct(api, { label: 'Operacion', stock: 2 });
  const customer = await newCustomer(api, 'Cliente Operacion UI6');
  const token = await adminLogin(api);
  let orderId = '';
  const operation = async () => {
    const res = await api.get('/api/admin/operations', {
      headers: auth(token), params: { order_id: orderId, kind: 'SEND_NOTIFICATION' },
    });
    return ((await res.json()) as { items: Array<{ status: string }> }).items[0];
  };

  await withServiceStopped('notification-service', async () => {
    const order = await createOrder(api, customer.token, product.id);
    orderId = order.id;
    expect((await payOrder(api, customer.token, order.id)).status).toBe('PAID');
    await expect.poll(async () => (await operation())?.status, { timeout: 300_000, intervals: [3_000] }).toBe('FAILED');
  });

  // FAILED operations wait in the slow lane (15 min): the admin retries now
  await signIn(page, token, `/admin/operaciones?pedido=${orderId}`);
  const row = page.locator('.operation', { hasText: 'Enviar notificación' });
  await expect(row.getByRole('button', { name: 'Reintentar' })).toBeVisible();
  await row.getByRole('button', { name: 'Reintentar' }).click();
  await expect(page.getByText('Reintento ejecutado: la operación se completó.')).toBeVisible({ timeout: 30_000 });
  expect((await operation())?.status).toBe('DONE');
  const notes = await api.get('/api/notifications?size=20', { headers: auth(customer.token) });
  const items = ((await notes.json()) as { items: Array<{ order_id: string }> }).items;
  expect(items.filter((n) => n.order_id === orderId)).toHaveLength(1); // exactly one, no duplicate
});
