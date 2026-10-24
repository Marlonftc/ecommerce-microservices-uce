// Catalogue, cart, checkout and the simulated payment (E2E build with the
// demonstration options). Each test uses its own customer and its own product.
import { test, expect, signIn } from '../support/fixtures';
import { available, getOrder, newCustomer, newProduct, putInCart } from '../support/api';
import type { Page } from '@playwright/test';

async function checkoutFromCart(page: Page): Promise<string> {
  await page.goto('/carrito');
  await page.getByRole('link', { name: 'Continuar con la compra' }).click();
  await page.getByRole('button', { name: 'Confirmar pedido' }).click();
  await expect(page).toHaveURL(/\/pedidos\/[0-9a-f-]{36}$/);
  await expect(page.getByText('Pendiente de pago', { exact: true })).toBeVisible();
  return page.url().split('/pedidos/')[1]!;
}

async function chooseDemo(page: Page, label: string) {
  await page.getByText('Opciones de demostración').click();
  await page.getByRole('radio', { name: label }).check();
}

test('catalogue search, product detail and cart editing', async ({ page, api }) => {
  const product = await newProduct(api, { label: 'Catalogo', stock: 5, price: '19.90' });
  const customer = await newCustomer(api, 'Cliente Catalogo UI6');
  await signIn(page, customer.token, '/productos');

  await page.getByLabel('Buscar por nombre').fill(product.name);
  await page.getByRole('button', { name: 'Buscar' }).click();
  await expect(page.getByText(/^1 producto/)).toBeVisible();
  await page.getByRole('link', { name: product.name }).click();
  await expect(page.getByRole('heading', { level: 1, name: product.name })).toBeVisible();
  await expect(page.getByText('5 unidades').first()).toBeVisible();

  await page.getByLabel('Cantidad').fill('2');
  await page.getByRole('button', { name: 'Agregar al carrito' }).click();
  await expect(page.getByText('Producto agregado. Tienes 2 en tu carrito.')).toBeVisible();
  await expect(page.getByRole('link', { name: 'Carrito, 2 unidades' })).toBeVisible();

  await page.goto('/carrito');
  await expect(page.getByRole('link', { name: product.name })).toBeVisible();
  await page.getByLabel(`Cantidad de ${product.name}`).fill('1');
  await page.getByRole('button', { name: 'Actualizar' }).click();
  await expect(page.getByText('Cantidad actualizada.')).toBeVisible();
  await expect(page.getByRole('link', { name: 'Carrito, 1 unidad' })).toBeVisible();
  await page.getByRole('button', { name: `Eliminar ${product.name}` }).click();
  await expect(page.getByText('Producto eliminado del carrito.')).toBeVisible();
  await expect(page.getByText('Tu carrito está vacío')).toBeVisible();
  expect(await available(api, product.id)).toBe(5); // nothing reserved by the cart
});

test('checkout and approved payment: paid order, empty cart, stock confirmed, bell at 1', async ({ page, api }) => {
  const product = await newProduct(api, { label: 'Compra', stock: 3 });
  const customer = await newCustomer(api, 'Cliente Compra UI6');
  await putInCart(api, customer.token, product.id, 2);
  await signIn(page, customer.token);

  const orderId = await checkoutFromCart(page);
  await expect(page.getByText(/para pagar; después la reserva se libera/)).toBeVisible();
  expect(await available(api, product.id)).toBe(1); // 2 reserved

  await page.getByRole('button', { name: /^Pagar/ }).click();
  await expect(page.getByText('Pago aprobado. Tu carrito se vació.')).toBeVisible();
  await expect(page.getByRole('link', { name: 'Carrito, 0 unidades' })).toBeVisible();
  const order = await getOrder(api, customer.token, orderId);
  expect([order.status, order.inventory_status]).toEqual(['PAID', 'CONFIRMED']);
  expect(await available(api, product.id)).toBe(1);

  // The notification is sent by order-worker: the bell updates without reloading
  await expect(page.getByRole('link', { name: 'Notificaciones, 1 sin leer' })).toBeVisible({ timeout: 60_000 });

  await page.goto('/pedidos');
  await expect(page.locator('.order-row').first()).toContainText('Pagado');
});

test('rejected payment keeps the cart; slow approval is settled in the background', async ({ page, api }) => {
  test.setTimeout(180_000);
  const product = await newProduct(api, { label: 'Pagos', stock: 4 });
  const customer = await newCustomer(api, 'Cliente Pagos UI6');
  await putInCart(api, customer.token, product.id, 1);
  await signIn(page, customer.token);

  // REJECT
  const rejectedId = await checkoutFromCart(page);
  await chooseDemo(page, 'Rechazar el pago');
  await page.getByRole('button', { name: /^Pagar/ }).click();
  await expect(page.getByText(/El pago fue rechazado\. Tu carrito se conservó/)).toBeVisible();
  expect((await getOrder(api, customer.token, rejectedId)).status).toBe('PAYMENT_REJECTED');
  await expect(page.getByRole('link', { name: 'Carrito, 1 unidad' })).toBeVisible();

  // SLOW_APPROVE: 202 now, the worker confirms it later and the page updates by itself
  const slowId = await checkoutFromCart(page);
  await chooseDemo(page, 'Aprobación lenta (se confirma en segundo plano)');
  await page.getByRole('button', { name: /^Pagar/ }).click();
  await expect(page.getByText(/Estamos confirmando tu pago/)).toBeVisible();
  await expect(page.getByText('Pago aprobado. Tu carrito se vació.')).toBeVisible({ timeout: 90_000 });
  expect((await getOrder(api, customer.token, slowId)).status).toBe('PAID');
});

test('cancel an order before paying: stock released', async ({ page, api }) => {
  const product = await newProduct(api, { label: 'Cancelar', stock: 2 });
  const customer = await newCustomer(api, 'Cliente Cancela UI6');
  await putInCart(api, customer.token, product.id, 2);
  await signIn(page, customer.token);

  const orderId = await checkoutFromCart(page);
  expect(await available(api, product.id)).toBe(0);
  await page.getByRole('button', { name: 'Cancelar pedido' }).click();
  await page.getByRole('button', { name: 'Sí, cancelar' }).click();
  await expect(page.getByText(/^Pedido cancelado\./)).toBeVisible();
  await expect(page.getByText(/^Lo cancelaste tú\./)).toBeVisible();
  const order = await getOrder(api, customer.token, orderId);
  expect(order.status).toBe('CANCELLED');
  // Released at once or by order-worker shortly after
  await expect.poll(() => available(api, product.id), { timeout: 60_000 }).toBe(2);
  // The cart is kept and unlocked
  await page.goto('/carrito');
  await expect(page.getByRole('button', { name: 'Vaciar carrito' })).toBeEnabled();
});
