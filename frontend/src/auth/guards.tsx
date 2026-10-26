// Route guards. They only decide what the interface shows: every protected call is
// still authorized by the microservices (401/403).
import { Navigate, Outlet, useLocation } from 'react-router-dom';
import type { Role } from '../api/types';
import { ErrorAlert } from '../components/Alert';
import { Button } from '../components/Button';
import { Spinner } from '../components/Spinner';
import { ForbiddenPage } from '../pages/ForbiddenPage';
import { useAuth } from './AuthContext';
import { safeReturnPath } from './session';

function SessionLoading() {
  return (
    <div className="session-wait">
      <Spinner label="Verificando tu sesión…" />
    </div>
  );
}

/** user-service did not answer: the session is neither accepted nor discarded */
function SessionUnavailable() {
  const { error, retry, logout } = useAuth();
  return (
    <section className="session-wait session-wait--error">
      <h1>No pudimos verificar tu sesión</h1>
      <ErrorAlert error={error ?? new Error('unknown')} />
      <div className="form-actions">
        <Button onClick={retry}>Reintentar</Button>
        <Button variant="secondary" onClick={logout}>
          Cerrar sesión
        </Button>
      </div>
    </section>
  );
}

/** Pages for any signed-in user. Without a session: to /login, then back here. */
export function RequireAuth() {
  const { status } = useAuth();
  const location = useLocation();
  if (status === 'loading') return <SessionLoading />;
  if (status === 'unavailable') return <SessionUnavailable />;
  if (status === 'anonymous') {
    return <Navigate to="/login" replace state={{ from: `${location.pathname}${location.search}` }} />;
  }
  return <Outlet />;
}

/** Nested inside RequireAuth. Another role gets a 403 page, never a redirect (no loops). */
export function RequireRole({ role }: { role: Role }) {
  const { user } = useAuth();
  if (!user || user.role !== role) return <ForbiddenPage />;
  return <Outlet />;
}

/**
 * Login and register. As soon as there is a session (also right after logging in or
 * registering on these pages) customers return to their destination or `fallback`;
 * administrators enter the management panel.
 * This guard is the only place that decides that destination, so two redirects
 * cannot race each other.
 */
export function GuestOnly({ fallback = '/' }: { fallback?: string }) {
  const { status, user } = useAuth();
  const location = useLocation();
  if (status === 'loading') return <SessionLoading />;
  if (status === 'authenticated') {
    const from = (location.state as { from?: unknown } | null)?.from;
    const destination = user?.role === 'admin' ? '/admin' : from === undefined ? fallback : safeReturnPath(from);
    return <Navigate to={destination} replace />;
  }
  return <Outlet />;
}
