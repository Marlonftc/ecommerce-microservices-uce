// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { makeToken } from '../test/tokens';
import {
  EXPIRY_MARGIN_MS, SESSION_KEY, clearSession, decodeClaims, isExpired, loadSession, safeReturnPath, saveSession,
} from './session';

afterEach(() => {
  sessionStorage.clear();
  vi.restoreAllMocks();
});

describe('decodeClaims', () => {
  it('reads sub, role and exp without verifying the signature', () => {
    const token = makeToken({ sub: '42', role: 'admin', exp: 2000000000 });
    expect(decodeClaims(token)).toEqual({ sub: '42', role: 'admin', exp: 2000000000 });
  });

  it.each(['', 'abc', 'a.b', 'a.%%%.c', `x.${btoa('{"sub":1}')}.y`, `x.${btoa('null')}.y`])('rejects %j', (token) => {
    expect(decodeClaims(token)).toBeNull();
  });
});

describe('expiry', () => {
  it('counts the 30 s margin', () => {
    const now = 1_000_000;
    expect(isExpired(now + EXPIRY_MARGIN_MS + 1, now)).toBe(false);
    expect(isExpired(now + EXPIRY_MARGIN_MS, now)).toBe(true);
    expect(isExpired(now - 1, now)).toBe(true);
  });
});

describe('storage', () => {
  it('stores only the token and its expiry', () => {
    const token = makeToken({ exp: 2000000000 });
    expect(saveSession(token)).toEqual({ token, expiresAt: 2000000000 * 1000 });
    expect(JSON.parse(sessionStorage.getItem(SESSION_KEY)!)).toEqual({ token, expiresAt: 2000000000000 });
    expect(loadSession()).toEqual({ token, expiresAt: 2000000000000 });
  });

  it('does not store a value that is not a JWT', () => {
    expect(saveSession('not-a-token')).toBeNull();
    expect(sessionStorage.getItem(SESSION_KEY)).toBeNull();
  });

  it('removes corrupt data', () => {
    sessionStorage.setItem(SESSION_KEY, '{broken');
    expect(loadSession()).toBeNull();
    expect(sessionStorage.getItem(SESSION_KEY)).toBeNull();
    sessionStorage.setItem(SESSION_KEY, JSON.stringify({ token: 'x', expiresAt: 1 }));
    expect(loadSession()).toBeNull();
  });

  it('works when the browser blocks storage', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new DOMException('blocked', 'SecurityError');
    });
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('blocked', 'SecurityError');
    });
    expect(loadSession()).toBeNull();
    expect(saveSession(makeToken({}))).not.toBeNull();
    expect(() => clearSession()).not.toThrow();
  });
});

describe('safeReturnPath', () => {
  it.each([
    ['/cuenta', '/cuenta'],
    ['/admin?x=1', '/admin?x=1'],
    ['//evil.example', '/'],
    ['/\\evil.example', '/'],
    ['https://evil.example', '/'],
    ['cuenta', '/'],
    ['/login', '/'],
    ['/registro?x', '/'],
    [undefined, '/'],
    [42, '/'],
  ])('%j -> %j', (value, expected) => {
    expect(safeReturnPath(value)).toBe(expected);
  });
});
