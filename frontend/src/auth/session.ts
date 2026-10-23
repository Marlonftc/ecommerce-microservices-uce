// Session kept in sessionStorage: only the token and its expiry time. It survives a
// page reload but not closing the tab, and it is not shared between tabs. No password
// or profile data is ever stored. Risk: a script injected in the page (XSS) could read
// it; the Content-Security-Policy reduces that risk but does not remove it.

export const SESSION_KEY = 'ecommerce-uce.session';
/** The session is considered expired this long before the real expiry */
export const EXPIRY_MARGIN_MS = 30_000;

export interface StoredSession {
  token: string;
  /** Expiry of the token in ms since epoch (claim exp) */
  expiresAt: number;
}

export interface TokenClaims {
  sub: string;
  role: string;
  exp: number;
}

function storage(): Storage | null {
  try {
    return window.sessionStorage;
  } catch {
    return null; // blocked by the browser
  }
}

function base64UrlDecode(part: string): string {
  const base64 = part.replace(/-/g, '+').replace(/_/g, '/').padEnd(Math.ceil(part.length / 4) * 4, '=');
  const binary = atob(base64);
  const bytes = Uint8Array.from(binary, (c) => c.charCodeAt(0));
  return new TextDecoder().decode(bytes);
}

/**
 * Reads the claims of a JWT WITHOUT verifying its signature. Only used by the
 * interface (expiry, role hint); the services verify the signature on every call.
 */
export function decodeClaims(token: string): TokenClaims | null {
  const parts = token.split('.');
  if (parts.length !== 3 || !parts[1]) return null;
  try {
    const payload: unknown = JSON.parse(base64UrlDecode(parts[1]));
    if (typeof payload !== 'object' || payload === null) return null;
    const { sub, role, exp } = payload as Record<string, unknown>;
    if (typeof sub !== 'string' || typeof role !== 'string' || typeof exp !== 'number') return null;
    return { sub, role, exp };
  } catch {
    return null;
  }
}

export function isExpired(expiresAt: number, now: number = Date.now()): boolean {
  return now >= expiresAt - EXPIRY_MARGIN_MS;
}

/** Stores the token; returns null if it cannot be read as a JWT */
export function saveSession(token: string): StoredSession | null {
  const claims = decodeClaims(token);
  if (!claims) return null;
  const session = { token, expiresAt: claims.exp * 1000 };
  try {
    storage()?.setItem(SESSION_KEY, JSON.stringify(session));
  } catch {
    // Storage full or blocked: the session still works until the page is reloaded
  }
  return session;
}

/** Reads the stored session; anything malformed is removed */
export function loadSession(): StoredSession | null {
  const store = storage();
  let raw: string | null = null;
  try {
    raw = store?.getItem(SESSION_KEY) ?? null;
  } catch {
    return null;
  }
  if (!raw) return null;
  try {
    const value: unknown = JSON.parse(raw);
    if (typeof value === 'object' && value !== null) {
      const { token, expiresAt } = value as Record<string, unknown>;
      if (typeof token === 'string' && typeof expiresAt === 'number' && decodeClaims(token)) {
        return { token, expiresAt };
      }
    }
  } catch {
    // fall through
  }
  clearSession();
  return null;
}

export function clearSession(): void {
  try {
    storage()?.removeItem(SESSION_KEY);
  } catch {
    // nothing else can be done
  }
}

/**
 * A path to return to after login: only internal paths of this app. Anything that
 * could leave the site ("//host", "/\\host", "https://…") or loop back to the
 * login pages becomes "/".
 */
export function safeReturnPath(value: unknown): string {
  if (typeof value !== 'string') return '/';
  if (!value.startsWith('/') || value.startsWith('//') || value.includes('\\')) return '/';
  if (/^\/(login|registro)(\/|\?|$)/.test(value)) return '/';
  return value;
}
