// React served by the gateway: direct reloads, unknown routes, headers and the CSP.
import { test, expect } from '../support/fixtures';
import { GATEWAY_URL } from '../support/env';

const NOT_FOUND = { detail: { code: 'NOT_FOUND', message: 'Route not found' } };
const CSP =
  "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; font-src 'self'; " +
  "connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'";

test('direct reload of application routes shows the page, never a 404', async ({ page }) => {
  const routes: Array<[string, RegExp | string]> = [
    ['/', /^Una tienda de tecnología/],
    ['/productos', 'Catálogo'],
    ['/login', 'Iniciar sesión'],
    ['/registro', 'Crear cuenta'],
  ];
  for (const [path, heading] of routes) {
    const response = await page.goto(path);
    expect(response?.status(), path).toBe(200);
    await expect(page.getByRole('heading', { level: 1, name: heading })).toBeVisible();
  }
  // Protected routes reloaded without a session: the application sends to /login
  for (const path of ['/carrito', '/pedidos', '/notificaciones', '/admin', '/admin/usuarios']) {
    const response = await page.goto(path);
    expect(response?.status(), path).toBe(200);
    await expect(page).toHaveURL(/\/login$/);
    await expect(page.getByRole('heading', { level: 1, name: 'Iniciar sesión' })).toBeVisible();
  }
});

test('an unknown route shows the not-found page of the application', async ({ page }) => {
  const response = await page.goto('/ruta-que-no-existe/123');
  expect(response?.status()).toBe(200);
  await expect(page.getByRole('heading', { name: 'No encontramos esta página' })).toBeVisible();
});

test('unknown /api routes and /internal stay JSON 404 from the gateway', async ({ request }) => {
  for (const path of ['/api/xyz', '/api/', '/api/PRODUCTS', '/internal/products/1', '/api/internal/products/1',
    '/api/products/%2e%2e/internal/products/1']) {
    const res = await request.get(path);
    expect(res.status(), path).toBe(404);
    expect(res.headers()['content-type'], path).toContain('application/json');
    expect(await res.json(), path).toEqual(NOT_FOUND);
  }
});

test('security headers and request id on HTML, assets, API and errors', async ({ request }) => {
  const html = await request.get('/carrito', { headers: { 'X-Request-ID': 'ui6-html-request-0001' } });
  const script = /src="(\/assets\/index-[\w-]+\.js)"/.exec(await html.text())?.[1];
  expect(script).toBeTruthy();
  const responses = {
    html,
    asset: await request.get(script!),
    api: await request.get('/api/products?limit=1'),
    'api 401': await request.get('/api/users/me'),
    'api 404': await request.get('/api/nope'),
    'post to the frontend (405)': await request.post('/carrito', { data: {} }),
  };
  expect(html.headers()['x-request-id']).toBe('ui6-html-request-0001');
  for (const [label, res] of Object.entries(responses)) {
    const h = res.headers();
    expect(h['content-security-policy'], label).toBe(CSP);
    expect(h['x-content-type-options'], label).toBe('nosniff');
    expect(h['x-frame-options'], label).toBe('DENY');
    expect(h['referrer-policy'], label).toBe('same-origin');
    expect(h['cross-origin-opener-policy'], label).toBe('same-origin');
    expect(h['x-request-id'], label).toMatch(/^[A-Za-z0-9-]{8,64}$/);
    expect(h['server'], label).toBe('nginx');
  }
  expect(responses['post to the frontend (405)'].status()).toBe(405);
  expect(responses.asset.headers()['cache-control']).toBe('public, max-age=31536000, immutable');
  expect(html.headers()['cache-control']).toBe('no-cache');
  // A missing asset is a 404, never index.html served as JavaScript
  const missing = await request.get('/assets/index-no-existe.js');
  expect(missing.status()).toBe(404);
  expect(await missing.text()).not.toContain('<div id="root">');
});

test('the CSP blocks inline code, and the check used by every test detects it', async ({ browser }) => {
  // Separate context: this page is expected to have a violation
  const context = await browser.newContext({ baseURL: GATEWAY_URL });
  const page = await context.newPage();
  const violations: string[] = [];
  await page.exposeFunction('__probeCsp', (d: string) => violations.push(d));
  await page.addInitScript(() => {
    document.addEventListener('securitypolicyviolation', (e) =>
      (window as unknown as { __probeCsp: (d: string) => void }).__probeCsp(e.violatedDirective));
  });
  await page.goto('/');
  await page.evaluate(() => {
    const s = document.createElement('script');
    s.textContent = 'window.__inlineRan = true';
    document.head.appendChild(s);
  });
  await expect.poll(() => violations.length).toBeGreaterThan(0);
  expect(violations[0]).toMatch(/^script-src/);
  expect(await page.evaluate(() => (window as unknown as { __inlineRan?: boolean }).__inlineRan)).toBeUndefined();
  await context.close();
});
