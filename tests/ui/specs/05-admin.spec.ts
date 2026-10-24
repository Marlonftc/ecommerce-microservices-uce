// Administration: products and inventory, accounts, buyers in orders and customer
// notifications. Everything is done on data created by this run.
import { test, expect, signIn } from '../support/fixtures';
import {
  adminLogin, auth, available, createOrder, login, newCustomer, newProduct, payOrder, recordProduct,
  uniqueSuffix, unreadCount,
} from '../support/api';
import type { Page } from '@playwright/test';

/** Accounts are listed by id, 20 per page, without search: the page is estimated from the id */
async function openUserRow(page: Page, userId: number, email: string) {
  for (let p = Math.floor((userId - 1) / 20) + 1; p >= 1; p--) {
    await page.goto(p > 1 ? `/admin/usuarios?pagina=${p}` : '/admin/usuarios');
    await expect(page.locator('.user-list, .empty-state').first()).toBeVisible();
    const row = page.locator('.user-row', { hasText: email });
    if (await row.count()) return row;
  }
  throw new Error(`account #${userId} not found in /admin/usuarios`);
}

test('admin creates, edits, deactivates and reactivates a product and adjusts its stock', async ({ page, api }) => {
  const token = await adminLogin(api);
  await signIn(page, token, '/admin/productos');
  await expect(page.getByRole('heading', { name: 'Productos', exact: true })).toBeVisible();

  const suffix = uniqueSuffix();
  const sku = `UI6-ADM-${suffix}`.toUpperCase();
  const name = `UI6 Producto admin ${suffix}`;
  await page.getByRole('link', { name: 'Nuevo producto' }).click();
  await page.getByLabel('SKU').fill(sku);
  await page.getByLabel('Nombre').fill(name);
  await page.getByLabel('Categoría').fill('pruebas-ui');
  await page.getByLabel('Precio (USD)').fill('12,5');
  await page.getByRole('button', { name: 'Crear producto' }).click();
  await expect(page.getByText('Producto creado. Registra sus existencias en el inventario.')).toBeVisible();
  await expect(page).toHaveURL(/\/admin\/productos\/\d+$/);
  const productId = Number(page.url().split('/').pop());
  recordProduct(productId);
  await expect(page.getByText('Sin registro de inventario')).toBeVisible();

  const adjust = async (mode: string, units: number) => {
    await page.getByRole('radio', { name: mode }).check();
    await page.getByRole('textbox', { name: 'Unidades' }).fill(String(units));
    await page.getByLabel('Motivo (opcional)').fill('Prueba de interfaz etapa 6');
    await page.getByRole('button', { name: 'Aplicar ajuste' }).click();
  };
  await adjust('Agregar unidades', 10);
  await expect(page.getByText(/Ajuste aplicado: agregar 10 unidades \(0 → 10 disponibles\)/)).toBeVisible();
  await adjust('Quitar unidades', 3);
  await expect(page.getByText(/\(10 → 7 disponibles\)/)).toBeVisible();

  // Lost answer: the adjustment reaches inventory-service but the browser never gets the
  // response. The retry reuses the same Idempotency-Key and the units are added once.
  let dropped = false;
  await page.route('**/api/inventory/*/adjustments', async (route) => {
    if (route.request().method() === 'POST' && !dropped) {
      dropped = true;
      await route.fetch(); // applied on the server
      await route.abort('failed');
    } else {
      await route.continue();
    }
  });
  await adjust('Agregar unidades', 5);
  await expect(page.getByText(/El ajuste pudo haberse aplicado/)).toBeVisible();
  await page.getByRole('button', { name: 'Reintentar con la misma clave' }).click();
  await expect(page.getByText(/Este ajuste ya estaba aplicado \(7 → 12 disponibles\); no se sumó de nuevo/)).toBeVisible();
  await page.unroute('**/api/inventory/*/adjustments');
  expect(await available(api, productId)).toBe(12);

  await page.getByLabel('Precio (USD)').fill('13.00');
  await page.getByRole('button', { name: 'Guardar cambios' }).click();
  await expect(page.getByText('Cambios guardados.')).toBeVisible();

  await page.getByRole('button', { name: 'Desactivar producto' }).click();
  await page.getByRole('button', { name: 'Sí, desactivar' }).click();
  await expect(page.getByText(/Producto desactivado/)).toBeVisible();
  expect((await api.get(`/api/products/${productId}`)).status()).toBe(404); // hidden from the public
  await page.getByRole('button', { name: 'Reactivar producto' }).click();
  await expect(page.getByText(/Producto reactivado/)).toBeVisible();
  const reactivated = (await (await api.get(`/api/products/${productId}`)).json()) as { price: string; sku: string };
  expect([reactivated.sku, reactivated.price]).toEqual([sku, '13.00']);
});

test('admin deactivates an account, which can no longer log in', async ({ page, api }) => {
  const customer = await newCustomer(api, 'Cliente Desactivar UI6');
  await signIn(page, await adminLogin(api));
  const row = await openUserRow(page, customer.id, customer.email);
  await row.getByRole('button', { name: 'Desactivar' }).click();
  await expect(page.getByText(/su token sigue aceptado hasta que venza/)).toBeVisible();
  await page.getByRole('button', { name: 'Confirmar' }).click();
  await expect(page.getByText(/Desactivar la cuenta: hecho\./)).toBeVisible();
  await expect(row.getByText('Inactiva')).toBeVisible();
  // The admin's own row has no actions
  const own = page.locator('.user-row', { hasText: 'Tu cuenta' });
  if (await own.count()) await expect(own.getByRole('button')).toHaveCount(0);

  await expect(login(api, customer.email, customer.password)).rejects.toThrow();
});

test('admin sees buyers in orders and customer notifications, read-only', async ({ page, api }) => {
  const product = await newProduct(api, { label: 'Comprador', stock: 2 });
  const customer = await newCustomer(api, `Comprador UI6 ${uniqueSuffix()}`);
  const order = await createOrder(api, customer.token, product.id);
  await payOrder(api, customer.token, order.id);
  await expect.poll(() => unreadCount(api, customer.token), { timeout: 60_000 }).toBe(1);

  await signIn(page, await adminLogin(api), '/pedidos');
  await expect(page.getByRole('heading', { name: 'Pedidos de todos los clientes' })).toBeVisible();
  await expect(page.locator('.order-row', { hasText: customer.fullName }).first()).toBeVisible();

  await page.goto(`/pedidos/${order.id}`);
  await expect(page.getByText(`(${customer.email})`)).toBeVisible();
  await expect(page.getByText(/Como administrador puedes consultarlo/)).toBeVisible();
  await expect(page.getByRole('button', { name: /^Pagar|Cancelar pedido/ })).toHaveCount(0);

  await page.goto('/admin/notificaciones');
  await expect(page.locator('.notification', { hasText: customer.fullName }).first()).toBeVisible();
  await expect(page.getByRole('button', { name: /Marcar/ })).toHaveCount(0);
  // Read-only: the customer's notification is still unread
  expect(await unreadCount(api, customer.token)).toBe(1);

  await page.goto(`/admin/operaciones?pedido=${order.id}`);
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
  expect((await api.get('/api/admin/operations', { headers: auth(await adminLogin(api)) })).status()).toBe(200);
});
