// Shared fixtures:
// - api: a request context for the gateway, closed after each test.
// - every page reports Content-Security-Policy violations and uncaught errors;
//   a test fails if there is any (the policy is never relaxed to make a test pass).
// - signIn(): opens a session the way the application does after a login.
import { test as base, expect, type APIRequestContext, type Locator, type Page } from '@playwright/test';
import { newApi } from './api';

export const SESSION_KEY = 'ecommerce-uce.session';

/** Claims of a JWT without verifying it (only exp is needed here) */
function tokenExpiry(token: string): number {
  const payload = token.split('.')[1] ?? '';
  const json = Buffer.from(payload.replace(/-/g, '+').replace(/_/g, '/'), 'base64').toString('utf8');
  return (JSON.parse(json) as { exp: number }).exp;
}

/**
 * Opens a session for an account whose token was obtained through the API.
 * storageState does not keep sessionStorage, so the value AuthContext stores after a
 * login ({ token, expiresAt } under "ecommerce-uce.session") is written in this tab and
 * the page is opened again: the application restores the session exactly as after a
 * reload. Nothing is written to disk.
 */
export async function signIn(page: Page, token: string, path = '/'): Promise<void> {
  await page.goto('/ruta-de-inicio-de-sesion-de-prueba');
  await page.evaluate(
    ([key, value]) => window.sessionStorage.setItem(key, value),
    [SESSION_KEY, JSON.stringify({ token, expiresAt: tokenExpiry(token) * 1000 })] as const,
  );
  await page.goto(path);
  await expect(page.getByRole('button', { name: 'Cerrar sesión' })).toBeVisible();
}

/**
 * Types a password without recording it: locator.fill() puts the typed value in the step
 * title of the HTML report. The value is set through the native setter and an input
 * event, which is what React listens to, so the form behaves as with real typing.
 */
export async function fillSecret(locator: Locator, value: string): Promise<void> {
  await locator.evaluate((element, secret) => {
    const input = element as HTMLInputElement;
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!;
    input.focus();
    setter.call(input, secret);
    input.dispatchEvent(new Event('input', { bubbles: true }));
  }, value);
}

interface Fixtures {
  api: APIRequestContext;
  pageProblems: string[];
}

export const test = base.extend<Fixtures>({
  api: async ({}, use) => {
    const api = await newApi();
    await use(api);
    await api.dispose();
  },

  pageProblems: [
    async ({ page }, use) => {
      const problems: string[] = [];
      await page.exposeFunction('__uiReportCsp', (directive: string, blocked: string) => {
        problems.push(`CSP violation: ${directive} blocked ${blocked}`);
      });
      await page.addInitScript(() => {
        document.addEventListener('securitypolicyviolation', (event) => {
          const report = (window as unknown as { __uiReportCsp: (d: string, b: string) => void }).__uiReportCsp;
          report(event.violatedDirective, event.blockedURI || 'inline');
        });
      });
      page.on('console', (message) => {
        if (message.type() === 'error' && /Content Security Policy/i.test(message.text())) {
          problems.push(`CSP console: ${message.text()}`);
        }
      });
      page.on('pageerror', (error) => problems.push(`uncaught: ${error.message}`));
      await use(problems);
      expect(problems, 'CSP violations or uncaught errors in the page').toEqual([]);
    },
    { auto: true },
  ],
});

export { expect };
