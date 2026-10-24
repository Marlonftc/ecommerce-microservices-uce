// Customer notifications: list, link to the order, mark as read, unread filter.
import { test, expect, signIn } from '../support/fixtures';
import { createOrder, newCustomer, newProduct, payOrder, unreadCount } from '../support/api';

test('a paid order produces a notification the customer can open and mark as read', async ({ page, api }) => {
  const product = await newProduct(api, { label: 'Notificacion', stock: 2 });
  const customer = await newCustomer(api, 'Cliente Avisos UI6');
  const order = await createOrder(api, customer.token, product.id);
  expect((await payOrder(api, customer.token, order.id)).status).toBe('PAID');
  // Sent by order-worker in the background
  await expect.poll(() => unreadCount(api, customer.token), { timeout: 60_000 }).toBe(1);

  await signIn(page, customer.token);
  const bell = page.getByRole('link', { name: 'Notificaciones, 1 sin leer' });
  await expect(bell).toBeVisible();
  await bell.click();
  await expect(page.getByRole('heading', { level: 1, name: 'Notificaciones' })).toBeVisible();
  const item = page.locator('.notification').first();
  await expect(item.getByText('Nueva')).toBeVisible();
  await expect(page.locator('.notification')).toHaveCount(1);

  await item.getByRole('link', { name: 'Ver pedido' }).click();
  await expect(page).toHaveURL(new RegExp(`/pedidos/${order.id}$`));
  await expect(page.getByText('Compra completada.')).toBeVisible();

  await page.goBack();
  await page.getByRole('button', { name: 'Marcar como leída' }).click();
  await expect(page.locator('.notification').first().getByText('Nueva')).toHaveCount(0);
  await expect(page.getByRole('link', { name: 'Notificaciones, 0 sin leer' })).toBeVisible();
  expect(await unreadCount(api, customer.token)).toBe(0);

  // The filter lives in the URL: click, then check the result
  const filter = page.getByRole('checkbox', { name: 'Solo no leídas' });
  await filter.click();
  await expect(page).toHaveURL(/no_leidas=1/);
  await expect(filter).toBeChecked();
  await expect(page.getByText('No tienes notificaciones sin leer')).toBeVisible();
});
