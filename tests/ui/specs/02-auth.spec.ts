// Registration, login, session restored on reload, logout and role checks.
// Only this file logs in through the form; each attempt is counted for the
// gateway limit of 10 logins per minute.
import { randomBytes } from 'node:crypto';
import { test, expect, fillSecret, signIn, SESSION_KEY } from '../support/fixtures';
import { auth, newCustomer, recordUser, reserveLogin, uniqueSuffix } from '../support/api';

async function fillLogin(page: import('@playwright/test').Page, email: string, password: string) {
  await page.getByLabel('Correo').fill(email);
  await fillSecret(page.getByLabel('Contraseña', { exact: true }), password);
  await reserveLogin();
  await page.getByRole('button', { name: 'Iniciar sesión' }).click();
}

test('register through the form, session kept on reload, then sign out', async ({ page, request }) => {
  const email = `ui6-${uniqueSuffix()}@example.com`;
  const password = `Ui6-${randomBytes(12).toString('base64url')}`;

  await page.goto('/registro');
  await page.getByLabel('Nombre completo').fill('Cliente Registro UI6');
  await page.getByLabel('Correo').fill(email);
  await fillSecret(page.getByLabel('Contraseña', { exact: true }), password);
  await fillSecret(page.getByLabel('Repite la contraseña'), password);
  await reserveLogin(); // registration logs in automatically
  await page.getByRole('button', { name: 'Crear cuenta' }).click();

  await expect(page).toHaveURL(/\/cuenta$/);
  await expect(page.getByRole('heading', { name: 'Mi cuenta' })).toBeVisible();
  await expect(page.getByText(email)).toBeVisible();

  // Kept only in this test's memory, to record the account for cleanup
  const stored = await page.evaluate((key) => window.sessionStorage.getItem(key), SESSION_KEY);
  const token = (JSON.parse(stored!) as { token: string }).token;
  const me = await request.get('/api/users/me', { headers: auth(token) });
  recordUser(((await me.json()) as { id: number }).id);
  // The password is never stored by the application
  expect(stored!.includes(password), 'password stored').toBe(false);

  await page.reload();
  await expect(page.getByRole('heading', { name: 'Mi cuenta' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Cerrar sesión' })).toBeVisible();

  await page.getByRole('button', { name: 'Cerrar sesión' }).click();
  await expect(page.getByRole('button', { name: 'Cerrar sesión' })).toHaveCount(0);
  expect(await page.evaluate(() => window.sessionStorage.length)).toBe(0);
  await page.goto('/carrito');
  await expect(page).toHaveURL(/\/login$/);
});

test('wrong password is rejected; a protected page leads to login and back', async ({ page, api }) => {
  const customer = await newCustomer(api, 'Cliente Login UI6');

  await page.goto('/login');
  await fillLogin(page, customer.email, 'contraseña-incorrecta');
  await expect(page.getByText('Correo o contraseña incorrectos, o la cuenta está desactivada.')).toBeVisible();
  await expect(page.getByLabel('Contraseña', { exact: true })).toHaveValue('');

  await page.goto('/pedidos');
  await expect(page).toHaveURL(/\/login$/);
  await fillLogin(page, customer.email, customer.password);
  await expect(page).toHaveURL(/\/pedidos$/);
  await expect(page.getByRole('heading', { name: 'Mis pedidos' })).toBeVisible();
});

test('a customer cannot open administration pages or admin APIs', async ({ page, api }) => {
  const customer = await newCustomer(api, 'Cliente Permisos UI6');
  await signIn(page, customer.token);
  await expect(page.getByRole('link', { name: 'Administración' })).toHaveCount(0);
  for (const path of ['/admin', '/admin/productos', '/admin/usuarios', '/admin/operaciones', '/admin/notificaciones']) {
    await page.goto(path);
    await expect(page.getByRole('heading', { name: 'No tienes permiso para ver esta página' }), path).toBeVisible();
  }
  // The services check the role again
  expect((await api.get('/api/users?limit=1', { headers: auth(customer.token) })).status()).toBe(403);
  expect((await api.get('/api/admin/operations', { headers: auth(customer.token) })).status()).toBe(403);
  const create = await api.post('/api/products', {
    headers: auth(customer.token),
    data: { sku: 'UI6-NO-PERMITIDO', name: 'No permitido', category: 'pruebas-ui', price: '1.00' },
  });
  expect(create.status()).toBe(403);
});
