// @vitest-environment jsdom
// Flow tests: the real routes, guards and session provider, with fetch answering
// like user-service behind the gateway.
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AppRoutes } from '../App';
import { http } from '../api/http';
import { makeToken } from '../test/tokens';
import { AuthProvider } from './AuthContext';
import { SESSION_KEY } from './session';

type Handler = (init: RequestInit | undefined) => Response | Promise<Response>;

const routes = new Map<string, Handler>();
const calls: { key: string; init: RequestInit | undefined }[] = [];

function json(status: number, body: unknown, headers: Record<string, string> = {}): Response {
  return new Response(body === undefined ? null : JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json', 'X-Request-ID': `test-${status}-req`, ...headers },
  });
}

function on(method: string, path: string, handler: Handler) {
  routes.set(`${method} ${path}`, handler);
}

const customer = { id: 7, email: 'ana@example.com', full_name: 'Ana Pérez', role: 'customer', is_active: true, created_at: '2026-10-01T10:00:00Z' };
const admin = { ...customer, id: 1, email: 'admin@example.com', full_name: 'Admin UCE', role: 'admin' };

function storeToken(token: string) {
  const exp = JSON.parse(Buffer.from(token.split('.')[1]!, 'base64url').toString()).exp as number;
  sessionStorage.setItem(SESSION_KEY, JSON.stringify({ token, expiresAt: exp * 1000 }));
}

function authHeader(init: RequestInit | undefined): string | undefined {
  return (init?.headers as Record<string, string> | undefined)?.Authorization;
}

function renderAt(path: string, state?: unknown) {
  return render(
    <MemoryRouter initialEntries={[state ? { pathname: path, state } : path]}>
      <AuthProvider>
        <AppRoutes />
      </AuthProvider>
    </MemoryRouter>,
  );
}

function fillLogin(email: string, password: string) {
  fireEvent.change(screen.getByLabelText('Correo'), { target: { value: email } });
  fireEvent.change(screen.getByLabelText('Contraseña'), { target: { value: password } });
  fireEvent.click(screen.getByRole('button', { name: 'Iniciar sesión' }));
}

beforeEach(() => {
  routes.clear();
  calls.length = 0;
  sessionStorage.clear();
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    const key = `${init?.method ?? 'GET'} ${url.split('?')[0]}`;
    calls.push({ key, init });
    const handler = routes.get(key);
    if (!handler) return json(404, { detail: { code: 'NOT_FOUND', message: 'Route not found' } });
    return handler(init);
  }));
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('restoring the session', () => {
  it('keeps the session after a reload: token from sessionStorage, profile from /users/me', async () => {
    const token = makeToken({ role: 'customer' });
    storeToken(token);
    on('GET', '/api/users/me', (init) => (authHeader(init) === `Bearer ${token}` ? json(200, customer) : json(401, {})));
    renderAt('/cuenta');
    expect(await screen.findByRole('heading', { name: 'Mi cuenta' })).toBeTruthy();
    expect(screen.getByText('ana@example.com')).toBeTruthy();
    expect(screen.getByText('Cliente')).toBeTruthy();
  });

  it('shows a loading state, never a redirect, while the profile is checked', async () => {
    storeToken(makeToken({}));
    let answer: (r: Response) => void = () => {};
    on('GET', '/api/users/me', () => new Promise<Response>((resolve) => { answer = resolve; }));
    renderAt('/cuenta');
    expect(await screen.findByText('Verificando tu sesión…')).toBeTruthy();
    expect(screen.queryByRole('heading', { name: 'Iniciar sesión' })).toBeNull();
    await act(async () => answer(json(200, customer)));
    expect(await screen.findByRole('heading', { name: 'Mi cuenta' })).toBeTruthy();
  });

  it('an expired token is discarded without calling /users/me', async () => {
    storeToken(makeToken({ exp: Math.floor(Date.now() / 1000) - 10 }));
    renderAt('/cuenta');
    expect(await screen.findByText('Tu sesión expiró. Inicia sesión de nuevo.')).toBeTruthy();
    expect(screen.getByRole('heading', { name: 'Iniciar sesión' })).toBeTruthy();
    expect(calls).toHaveLength(0);
    expect(sessionStorage.getItem(SESSION_KEY)).toBeNull();
  });

  it('deactivated account with a still valid token: session closed with its own message', async () => {
    storeToken(makeToken({}));
    on('GET', '/api/users/me', () => json(401, { detail: 'User not found or inactive' }));
    renderAt('/cuenta');
    expect(await screen.findByText(/Tu cuenta no está activa o ya no existe/)).toBeTruthy();
    expect(screen.queryByText(/Tu sesión expiró/)).toBeNull();
    expect(sessionStorage.getItem(SESSION_KEY)).toBeNull();
  });

  it('/users/me unavailable: the token is kept and the user can retry', async () => {
    const token = makeToken({});
    storeToken(token);
    let up = false;
    on('GET', '/api/users/me', () => (up ? json(200, customer) : json(503, { detail: 'down' })));
    renderAt('/cuenta');
    expect(await screen.findByText('No pudimos verificar tu sesión')).toBeTruthy();
    expect(screen.getByText(/Referencia: test-503-req/)).toBeTruthy();
    expect(JSON.parse(sessionStorage.getItem(SESSION_KEY)!).token).toBe(token);
    up = true;
    fireEvent.click(screen.getByRole('button', { name: 'Reintentar' }));
    expect(await screen.findByRole('heading', { name: 'Mi cuenta' })).toBeTruthy();
  });

  it('role in the token different from the profile: a new login is required', async () => {
    storeToken(makeToken({ role: 'admin' }));
    on('GET', '/api/users/me', () => json(200, customer));
    renderAt('/admin');
    expect(await screen.findByText('Tus permisos cambiaron. Inicia sesión de nuevo para continuar.')).toBeTruthy();
    expect(screen.queryByRole('heading', { name: 'Administración' })).toBeNull();
    expect(sessionStorage.getItem(SESSION_KEY)).toBeNull();
  });
});

describe('login', () => {
  it('protected page -> login -> back to the page; only the token is stored', async () => {
    const token = makeToken({});
    on('POST', '/api/auth/login', () => json(200, { access_token: token, token_type: 'bearer', expires_in: 1800 }));
    on('GET', '/api/users/me', () => json(200, customer));
    renderAt('/cuenta');
    expect(await screen.findByRole('heading', { name: 'Iniciar sesión' })).toBeTruthy();
    fillLogin('  ana@example.com ', 'secreto123');
    expect(await screen.findByRole('heading', { name: 'Mi cuenta' })).toBeTruthy();
    const loginCall = calls.find((c) => c.key === 'POST /api/auth/login')!;
    expect(JSON.parse(String(loginCall.init?.body))).toEqual({ email: 'ana@example.com', password: 'secreto123' });
    expect(authHeader(loginCall.init)).toBeUndefined();
    const stored = sessionStorage.getItem(SESSION_KEY)!;
    expect(JSON.parse(stored)).toEqual({ token, expiresAt: expect.any(Number) });
    expect(stored).not.toContain('secreto123');
    expect(stored).not.toContain('ana@example.com');
  });

  it('401 at login means wrong credentials, not an expired session', async () => {
    on('POST', '/api/auth/login', () => json(401, { detail: 'Invalid credentials' }));
    renderAt('/login');
    fillLogin('ana@example.com', 'mala');
    expect(await screen.findByText('Correo o contraseña incorrectos, o la cuenta está desactivada.')).toBeTruthy();
    expect(screen.queryByText(/expiró/)).toBeNull();
    expect((screen.getByLabelText('Contraseña') as HTMLInputElement).value).toBe('');
    expect(sessionStorage.getItem(SESSION_KEY)).toBeNull();
  });

  it('429 with Retry-After shows a countdown and blocks the button', async () => {
    on('POST', '/api/auth/login', () => json(429, { detail: { code: 'TOO_MANY_REQUESTS', message: 'x' } }, { 'Retry-After': '6' }));
    renderAt('/login');
    fillLogin('ana@example.com', 'x');
    expect(await screen.findByText(/Podrás intentarlo de nuevo en [56] s\./)).toBeTruthy();
    fireEvent.change(screen.getByLabelText('Contraseña'), { target: { value: 'y' } });
    expect((screen.getByRole('button', { name: 'Iniciar sesión' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('429 without Retry-After does not invent a waiting time', async () => {
    on('POST', '/api/auth/login', () => json(429, { detail: { code: 'TOO_MANY_REQUESTS', message: 'x' } }));
    renderAt('/login');
    fillLogin('ana@example.com', 'x');
    expect(await screen.findByText('Espera un momento antes de intentarlo de nuevo.')).toBeTruthy();
    expect(screen.queryByText(/ s\./)).toBeNull();
  });

  it('a signed-in user visiting /login goes home; an external return path is ignored', async () => {
    storeToken(makeToken({}));
    on('GET', '/api/users/me', () => json(200, customer));
    renderAt('/login', { from: '//evil.example/steal' });
    expect(await screen.findByRole('heading', { level: 1, name: 'Tecnología para lo que viene.' })).toBeTruthy();
  });
});

describe('roles', () => {
  it('a customer gets a 403 page at /admin (no redirect loop)', async () => {
    storeToken(makeToken({ role: 'customer' }));
    on('GET', '/api/users/me', () => json(200, customer));
    renderAt('/admin');
    expect(await screen.findByRole('heading', { name: 'No tienes permiso para ver esta página' })).toBeTruthy();
    expect(screen.queryByRole('link', { name: 'Panel principal' })).toBeNull();
  });

  it('an admin sees the admin page and its link; no admin endpoint is called', async () => {
    storeToken(makeToken({ role: 'admin', sub: '1' }));
    on('GET', '/api/users/me', () => json(200, admin));
    renderAt('/admin');
    expect(await screen.findByRole('heading', { name: 'Administración' })).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Panel principal' })).toBeTruthy();
    // Since stage 3 the cart of every signed-in user is read; no admin endpoint is called
    expect(calls.map((c) => c.key).sort()).toEqual(['GET /api/cart', 'GET /api/users/me']);
  });

  it('anonymous at /admin is sent to login', async () => {
    renderAt('/admin');
    expect(await screen.findByRole('heading', { name: 'Iniciar sesión' })).toBeTruthy();
  });
});

describe('logout and expiry', () => {
  it('logout clears storage, memory and the token of later requests', async () => {
    storeToken(makeToken({}));
    on('GET', '/api/users/me', () => json(200, customer));
    renderAt('/cuenta');
    fireEvent.click(await screen.findByRole('button', { name: 'Cerrar sesión' }));
    expect(await screen.findByRole('link', { name: 'Iniciar sesión' })).toBeTruthy();
    expect(screen.queryByText('Ana Pérez')).toBeNull();
    expect(sessionStorage.getItem(SESSION_KEY)).toBeNull();
    on('GET', '/api/products', () => json(200, { items: [], total: 0, limit: 20, offset: 0 }));
    await http.get('/products');
    expect(authHeader(calls.at(-1)!.init)).toBeUndefined();
  });

  it('the session ends by itself when the token expires', async () => {
    // exp in 31 s; with the 30 s margin the timer fires after ~1 s
    storeToken(makeToken({ exp: Math.floor(Date.now() / 1000) + 31 }));
    on('GET', '/api/users/me', () => json(200, customer));
    renderAt('/cuenta');
    expect(await screen.findByRole('heading', { name: 'Mi cuenta' })).toBeTruthy();
    expect(await screen.findByText('Tu sesión expiró. Inicia sesión de nuevo.', {}, { timeout: 4000 })).toBeTruthy();
    expect(sessionStorage.getItem(SESSION_KEY)).toBeNull();
  });

  it('a 401 answering an OLD session does not close the new one', async () => {
    const oldToken = makeToken({}, 'old');
    const newToken = makeToken({}, 'new');
    storeToken(oldToken);
    on('GET', '/api/users/me', () => json(200, customer));
    renderAt('/cuenta');
    await screen.findByRole('heading', { name: 'Mi cuenta' });

    // A slow request sent with the old token
    let answerOld: (r: Response) => void = () => {};
    on('GET', '/api/orders', () => new Promise<Response>((resolve) => { answerOld = resolve; }));
    const slow = http.get('/orders').catch(() => null);

    // Logout and login again: new token
    fireEvent.click(screen.getByRole('button', { name: 'Cerrar sesión' }));
    on('POST', '/api/auth/login', () => json(200, { access_token: newToken, token_type: 'bearer', expires_in: 1800 }));
    await act(async () => {
      await screen.findByRole('link', { name: 'Iniciar sesión' });
    });
    fireEvent.click(screen.getByRole('link', { name: 'Iniciar sesión' }));
    fillLogin('ana@example.com', 'secreto123');
    await screen.findByText('Ana Pérez');

    // The old request finally answers 401
    await act(async () => {
      answerOld(json(401, { detail: 'Invalid or expired token' }));
      await slow;
    });
    expect(screen.getByText('Ana Pérez')).toBeTruthy();
    expect(JSON.parse(sessionStorage.getItem(SESSION_KEY)!).token).toBe(newToken);
  });

  it('a 401 for the CURRENT token closes the session as invalid', async () => {
    storeToken(makeToken({}));
    on('GET', '/api/users/me', () => json(200, customer));
    renderAt('/cuenta');
    await screen.findByRole('heading', { name: 'Mi cuenta' });
    on('GET', '/api/orders', () => json(401, { detail: 'Invalid or expired token' }));
    await act(async () => {
      await http.get('/orders').catch(() => null);
    });
    expect(await screen.findByText('Tu sesión ya no es válida. Inicia sesión de nuevo.')).toBeTruthy();
  });
});

describe('register', () => {
  function fillRegister(password = 'secreto123') {
    fireEvent.change(screen.getByLabelText('Nombre completo'), { target: { value: '  Ana Pérez ' } });
    fireEvent.change(screen.getByLabelText('Correo'), { target: { value: 'ana@example.com' } });
    fireEvent.change(screen.getByLabelText('Contraseña'), { target: { value: password } });
    fireEvent.change(screen.getByLabelText('Repite la contraseña'), { target: { value: password } });
    fireEvent.click(screen.getByRole('button', { name: 'Crear cuenta' }));
  }

  it('creates the account and logs in automatically', async () => {
    on('POST', '/api/auth/register', () => json(201, customer));
    on('POST', '/api/auth/login', () => json(200, { access_token: makeToken({}), token_type: 'bearer', expires_in: 1800 }));
    on('GET', '/api/users/me', () => json(200, customer));
    renderAt('/registro');
    fillRegister();
    expect(await screen.findByRole('heading', { name: 'Mi cuenta' })).toBeTruthy();
    const body = JSON.parse(String(calls.find((c) => c.key === 'POST /api/auth/register')!.init?.body));
    expect(body).toEqual({ email: 'ana@example.com', full_name: 'Ana Pérez', password: 'secreto123' });
  });

  it('account created but the automatic login fails: not registered again, manual login offered', async () => {
    on('POST', '/api/auth/register', () => json(201, customer));
    on('POST', '/api/auth/login', () => json(429, { detail: { code: 'TOO_MANY_REQUESTS', message: 'x' } }));
    renderAt('/registro');
    fillRegister();
    expect(await screen.findByText('Tu cuenta se creó correctamente.')).toBeTruthy();
    expect(screen.getByText(/demasiados intentos/)).toBeTruthy();
    expect(screen.getByRole('heading', { name: 'Iniciar sesión' })).toBeTruthy();
    expect((screen.getByLabelText('Correo') as HTMLInputElement).value).toBe('ana@example.com');
    expect(calls.filter((c) => c.key === 'POST /api/auth/register')).toHaveLength(1);
  });

  it('409: the email is already registered', async () => {
    on('POST', '/api/auth/register', () => json(409, { detail: 'Email already registered' }));
    renderAt('/registro');
    fillRegister();
    expect(await screen.findByText('Ya existe una cuenta con este correo.')).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Iniciar sesión con este correo' })).toBeTruthy();
  });

  it('422 from the server is shown on its field', async () => {
    on('POST', '/api/auth/register', () =>
      json(422, { detail: [{ loc: ['body', 'email'], msg: 'value is not a valid email address', type: 'value_error' }] }));
    renderAt('/registro');
    fillRegister();
    expect(await screen.findByText('El servicio rechazó este valor. Revísalo.')).toBeTruthy();
  });

  it('a multibyte password over 72 bytes is stopped before sending', async () => {
    renderAt('/registro');
    fillRegister('ñ'.repeat(37));
    expect(await screen.findByText(/máximo 72 bytes/)).toBeTruthy();
    expect(calls).toHaveLength(0);
  });
});
