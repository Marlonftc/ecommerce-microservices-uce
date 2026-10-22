// Session state of the app. The profile and the role always come from
// GET /api/users/me (verified by user-service); the JWT is only used to send
// requests and to know when it expires. Final authorization stays in the services.
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import * as authApi from '../api/auth';
import { ApiError } from '../api/errors';
import { setTokenProvider, setUnauthorizedHandler } from '../api/http';
import type { RegisterRequest, UserOut } from '../api/types';
import {
  EXPIRY_MARGIN_MS, clearSession, decodeClaims, isExpired, loadSession, saveSession, type StoredSession,
} from './session';

export type AuthStatus = 'loading' | 'anonymous' | 'authenticated' | 'unavailable';
/** Why the last session ended (shown on the login page) */
export type SessionNotice = 'expired' | 'invalid' | 'deactivated' | 'role-changed';

interface AuthState {
  status: AuthStatus;
  user: UserOut | null;
  notice: SessionNotice | null;
  /** Set when the profile could not be checked (service down, network) */
  error: ApiError | null;
}

export type RegisterResult = { loggedIn: true } | { loggedIn: false; error: unknown };

export interface AuthContextValue extends AuthState {
  /** Expiry of the current token (ms since epoch), or null */
  expiresAt: number | null;
  login(email: string, password: string): Promise<void>;
  register(data: RegisterRequest): Promise<RegisterResult>;
  logout(): void;
  /** Checks the stored session again after a failure of user-service */
  retry(): void;
  clearNotice(): void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export const NOTICE_MESSAGES: Record<SessionNotice, string> = {
  expired: 'Tu sesión expiró. Inicia sesión de nuevo.',
  invalid: 'Tu sesión ya no es válida. Inicia sesión de nuevo.',
  deactivated: 'Tu cuenta no está activa o ya no existe. Si crees que es un error, contacta al administrador.',
  'role-changed': 'Tus permisos cambiaron. Inicia sesión de nuevo para continuar.',
};

/** Classifies a 401 received with a token: expired, deactivated account or invalid */
function noticeFor(error: ApiError, session: StoredSession): SessionNotice {
  if (isExpired(session.expiresAt)) return 'expired';
  if (error.detail && /inactive|not found/i.test(error.detail)) return 'deactivated';
  return 'invalid';
}

type VerifyOutcome = 'authenticated' | 'ended' | 'unavailable' | 'replaced';

export function AuthProvider({ children }: { children: ReactNode }) {
  const sessionRef = useRef<StoredSession | null>(null);
  const [state, setState] = useState<AuthState>({ status: 'loading', user: null, notice: null, error: null });

  const endSession = useCallback((notice: SessionNotice | null) => {
    sessionRef.current = null;
    clearSession();
    setState({ status: 'anonymous', user: null, notice, error: null });
  }, []);

  // The HTTP client reads the token from here; a 401 ends the session only if it
  // answers a request sent with the CURRENT token (an older answer is ignored).
  useEffect(() => {
    setTokenProvider(() => sessionRef.current?.token ?? null);
    setUnauthorizedHandler((error, sentToken) => {
      const current = sessionRef.current;
      if (!current || current.token !== sentToken) return;
      endSession(noticeFor(error, current));
    });
    return () => {
      setTokenProvider(() => null);
      setUnauthorizedHandler(null);
    };
  }, [endSession]);

  /** Confirms a session with GET /users/me before any protected page is shown */
  const verify = useCallback(
    async (session: StoredSession): Promise<{ outcome: VerifyOutcome; error?: unknown }> => {
      sessionRef.current = session;
      setState((s) => ({ ...s, status: 'loading', error: null }));
      try {
        const user = await authApi.me();
        if (sessionRef.current !== session) return { outcome: 'replaced' };
        // The token was issued with another role: permissions must be refreshed
        if (decodeClaims(session.token)?.role !== user.role) {
          endSession('role-changed');
          return { outcome: 'ended' };
        }
        setState({ status: 'authenticated', user, notice: null, error: null });
        return { outcome: 'authenticated' };
      } catch (error) {
        if (sessionRef.current !== session) return { outcome: 'ended', error };
        if (error instanceof ApiError && error.status === 401) {
          endSession(noticeFor(error, session));
          return { outcome: 'ended', error };
        }
        // user-service down or no network: the session is kept, the user can retry
        setState({ status: 'unavailable', user: null, notice: null, error: error instanceof ApiError ? error : null });
        return { outcome: 'unavailable', error };
      }
    },
    [endSession],
  );

  // On load: restore the session stored in this tab, if any
  useEffect(() => {
    const stored = loadSession();
    if (!stored) {
      setState({ status: 'anonymous', user: null, notice: null, error: null });
      return;
    }
    if (isExpired(stored.expiresAt)) {
      clearSession();
      setState({ status: 'anonymous', user: null, notice: 'expired', error: null });
      return;
    }
    void verify(stored);
  }, [verify]);

  // Ends the session when the token expires
  useEffect(() => {
    const session = sessionRef.current;
    if (!session || (state.status !== 'authenticated' && state.status !== 'unavailable')) return undefined;
    const delay = Math.max(0, session.expiresAt - EXPIRY_MARGIN_MS - Date.now());
    const timer = setTimeout(() => {
      if (sessionRef.current === session) endSession('expired');
    }, delay);
    return () => clearTimeout(timer);
  }, [state.status, endSession]);

  const login = useCallback(
    async (email: string, password: string) => {
      const token = (await authApi.login({ email: email.trim(), password })).access_token;
      const session = saveSession(token);
      if (!session) throw new Error('The login answer did not contain a valid token');
      const result = await verify(session);
      if (result.outcome !== 'authenticated') throw result.error ?? new Error('The session could not be confirmed');
    },
    [verify],
  );

  const register = useCallback(
    async (data: RegisterRequest): Promise<RegisterResult> => {
      await authApi.register(data); // errors (409, 422…) go to the form
      try {
        await login(data.email, data.password);
        return { loggedIn: true };
      } catch (error) {
        // The account exists: it is never registered again, the user logs in manually
        return { loggedIn: false, error };
      }
    },
    [login],
  );

  const logout = useCallback(() => endSession(null), [endSession]);

  const retry = useCallback(() => {
    const session = sessionRef.current ?? loadSession();
    if (session) void verify(session);
    else setState({ status: 'anonymous', user: null, notice: null, error: null });
  }, [verify]);

  const clearNotice = useCallback(() => setState((s) => ({ ...s, notice: null })), []);

  const value = useMemo<AuthContextValue>(
    () => ({
      ...state,
      expiresAt: state.status === 'authenticated' ? sessionRef.current?.expiresAt ?? null : null,
      login,
      register,
      logout,
      retry,
      clearNotice,
    }),
    [state, login, register, logout, retry, clearNotice],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error('useAuth must be used inside <AuthProvider>');
  return value;
}
