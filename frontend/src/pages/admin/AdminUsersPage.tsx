// Accounts for an admin: 20 per page (user-service returns a plain list, without total and
// without search), activate or deactivate, and change the role. Every change asks for a
// confirmation in the page. The admin's own row has no actions: user-service refuses any
// change to one's own account (CANNOT_MODIFY_SELF).
//
// PATCH sends the final value, so an unknown outcome is checked with GET /users?ids= and is
// never repeated by itself. What is observed is reported as the current state, without
// claiming that this request produced it (another admin may have changed it meanwhile).
import { useEffect, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { ApiError } from '../../api/errors';
import type { UserOut } from '../../api/types';
import { getUsersByIds, listUsers, updateUser, type UserChange } from '../../api/users';
import { useAuth } from '../../auth/AuthContext';
import { useForbiddenCheck } from '../../admin/adminSession';
import { isUncertain } from '../../admin/adminErrors';
import { readCatalogParams } from '../../catalog/params';
import { Alert, ErrorAlert } from '../../components/Alert';
import { Button } from '../../components/Button';
import { EmptyState } from '../../components/EmptyState';
import { Spinner } from '../../components/Spinner';
import { StatusBadge } from '../../components/StatusBadge';
import { formatDateTime } from '../../utils/format';

export const ADMIN_USERS_PAGE_SIZE = 20;

type Result = { status: 'loading' } | { status: 'ready'; users: UserOut[] } | { status: 'error'; error: unknown };

interface Pending {
  user: UserOut;
  change: UserChange;
}

interface Notice {
  userId: number;
  tone: 'success' | 'info' | 'warning' | 'error';
  text: string;
  /** The outcome is unknown: offer to read the account again */
  check?: Pending;
}

function describeChange(change: UserChange): string {
  if ('is_active' in change) return change.is_active ? 'Activar la cuenta' : 'Desactivar la cuenta';
  return change.role === 'admin' ? 'Hacer administrador' : 'Quitar el rol de administrador';
}

/** Consequences, including decision D11 (tokens stay valid until they expire) */
function effectOf(change: UserChange): string {
  if ('is_active' in change) {
    return change.is_active
      ? 'Podrá volver a iniciar sesión.'
      : 'No podrá iniciar sesión. Su sesión deja de valer en user-service de inmediato; en los demás servicios su token sigue aceptado hasta que venza (máximo 30 minutos).';
  }
  return change.role === 'admin'
    ? 'Tendrá acceso de administrador. Para usarlo en todos los servicios debe iniciar sesión de nuevo.'
    : 'Pierde el acceso de administrador en user-service de inmediato; en los demás servicios conserva esos permisos hasta que su token venza (máximo 30 minutos).';
}

const holds = (user: UserOut, change: UserChange) => ('is_active' in change ? user.is_active === change.is_active : user.role === change.role);

const CONFLICTS: Record<string, string> = {
  CANNOT_MODIFY_SELF: 'No puedes modificar tu propia cuenta.',
  LAST_ACTIVE_ADMIN: 'No se aplicó: debe quedar al menos un administrador activo.',
};

export function AdminUsersPage() {
  const { user: me } = useAuth();
  const onForbidden = useForbiddenCheck();
  const [search] = useSearchParams();
  const { page } = readCatalogParams(search);
  const [attempt, setAttempt] = useState(0);
  const [result, setResult] = useState<Result>({ status: 'loading' });
  const [pending, setPending] = useState<Pending | null>(null);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);
  const busyRef = useRef(false);

  useEffect(() => {
    const controller = new AbortController();
    setResult({ status: 'loading' });
    listUsers(ADMIN_USERS_PAGE_SIZE, (page - 1) * ADMIN_USERS_PAGE_SIZE, controller.signal)
      .then((users) => {
        if (!controller.signal.aborted) setResult({ status: 'ready', users });
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        if (!onForbidden(error)) setResult({ status: 'error', error });
      });
    return () => controller.abort();
  }, [page, attempt, onForbidden]);

  const replaceRow = (updated: UserOut) =>
    setResult((r) => (r.status === 'ready' ? { ...r, users: r.users.map((u) => (u.id === updated.id ? updated : u)) } : r));

  async function exclusive(userId: number, task: () => Promise<void>) {
    if (busyRef.current) return; // one change at a time; a double click sends one request
    busyRef.current = true;
    setBusyId(userId);
    try {
      await task();
    } finally {
      busyRef.current = false;
      setBusyId(null);
    }
  }

  const apply = ({ user, change }: Pending) =>
    exclusive(user.id, async () => {
      setPending(null);
      setNotice(null);
      try {
        const updated = await updateUser(user.id, change);
        replaceRow(updated);
        setNotice({ userId: user.id, tone: 'success', text: `${describeChange(change)}: hecho. ${effectOf(change)}` });
      } catch (error) {
        if (onForbidden(error)) return;
        if (error instanceof ApiError && error.status === 409 && error.code && CONFLICTS[error.code]) {
          setNotice({ userId: user.id, tone: 'error', text: CONFLICTS[error.code]! });
        } else if (error instanceof ApiError && error.status === 404) {
          setNotice({ userId: user.id, tone: 'error', text: 'La cuenta ya no existe. La lista se actualizó.' });
          setAttempt((n) => n + 1);
        } else if (isUncertain(error)) {
          setNotice({
            userId: user.id,
            tone: 'warning',
            text: 'No recibimos la respuesta: no sabemos si el cambio se aplicó. No se volverá a enviar solo.',
            check: { user, change },
          });
        } else {
          setNotice({ userId: user.id, tone: 'error', text: error instanceof ApiError ? error.message : 'No se pudo aplicar el cambio.' });
        }
      }
    });

  /** Reads the account again after an unknown outcome; never repeats the PATCH */
  const check = ({ user, change }: Pending) =>
    exclusive(user.id, async () => {
      try {
        const [current] = await getUsersByIds([user.id]);
        if (!current) {
          setNotice({ userId: user.id, tone: 'error', text: 'La cuenta ya no existe.' });
          setAttempt((n) => n + 1);
          return;
        }
        replaceRow(current);
        const state = `${current.is_active ? 'activa' : 'inactiva'}, ${current.role === 'admin' ? 'administrador' : 'cliente'}`;
        setNotice(
          holds(current, change)
            ? {
                userId: user.id,
                tone: 'info',
                text: `Estado actual: ${state}. Ya tiene el valor solicitado (pudo aplicarlo esta solicitud u otro cambio simultáneo).`,
              }
            : {
                userId: user.id,
                tone: 'info',
                text: `Estado actual: ${state}. No tiene el valor solicitado; puedes volver a intentarlo.`,
              },
        );
      } catch (error) {
        if (onForbidden(error)) return;
        setNotice({ userId: user.id, tone: 'warning', text: 'Tampoco pudimos consultar la cuenta. Inténtalo más tarde.', check: { user, change } });
      }
    });

  const users = result.status === 'ready' ? result.users : [];

  return (
    <section className="admin-users">
      <nav className="breadcrumb" aria-label="Ruta">
        <Link to="/admin">Administración</Link>
        <span aria-hidden="true">/</span>
        <span>Usuarios</span>
      </nav>
      <header className="page-header">
        <h1>Usuarios</h1>
        <p className="page-header__lead">Cuentas ordenadas por fecha de registro. user-service no ofrece búsqueda: se recorren por páginas.</p>
      </header>

      {result.status === 'loading' && (
        <div className="catalog__state">
          <Spinner label="Cargando usuarios…" />
        </div>
      )}
      {result.status === 'error' && (
        <div className="catalog__state">
          <ErrorAlert error={result.error} title="No pudimos cargar los usuarios." />
          <Button variant="secondary" onClick={() => setAttempt((n) => n + 1)}>
            Reintentar
          </Button>
        </div>
      )}
      {result.status === 'ready' && users.length === 0 && (
        <EmptyState
          title={page === 1 ? 'No hay cuentas' : 'No hay más cuentas'}
          action={
            page > 1 ? (
              <Link to="/admin/usuarios" className="btn btn--secondary">
                Ir a la primera página
              </Link>
            ) : undefined
          }
        />
      )}
      {users.length > 0 && (
        <ul className="user-list" aria-label="Usuarios">
          {users.map((account) => {
            const own = account.id === me?.id;
            const rowNotice = notice?.userId === account.id ? notice : null;
            const confirming = pending?.user.id === account.id ? pending : null;
            const busy = busyId === account.id;
            return (
              <li key={account.id} className="user-row">
                <div className="user-row__main">
                  <span className="user-row__name">{account.full_name}</span>
                  <span className="user-row__email">{account.email}</span>
                  <span className="user-row__meta">
                    #{account.id} · alta {formatDateTime(account.created_at)}
                  </span>
                </div>
                <div className="user-row__badges">
                  <StatusBadge tone={account.role === 'admin' ? 'info' : 'neutral'} label={account.role === 'admin' ? 'Administrador' : 'Cliente'} />
                  <StatusBadge tone={account.is_active ? 'success' : 'danger'} label={account.is_active ? 'Activa' : 'Inactiva'} />
                </div>
                <div className="user-row__actions">
                  {own ? (
                    <span className="user-row__own">Tu cuenta</span>
                  ) : (
                    <>
                      <Button
                        variant="secondary"
                        disabled={busy || busyId !== null}
                        onClick={() => setPending({ user: account, change: { is_active: !account.is_active } })}
                      >
                        {account.is_active ? 'Desactivar' : 'Activar'}
                      </Button>
                      <Button
                        variant="ghost"
                        disabled={busy || busyId !== null}
                        onClick={() => setPending({ user: account, change: { role: account.role === 'admin' ? 'customer' : 'admin' } })}
                      >
                        {account.role === 'admin' ? 'Quitar rol de administrador' : 'Hacer administrador'}
                      </Button>
                    </>
                  )}
                </div>
                {confirming && (
                  <div className="inline-confirm user-row__wide" role="group" aria-label="Confirmar cambio">
                    <p>
                      <strong>
                        {describeChange(confirming.change)} de {account.full_name}.
                      </strong>{' '}
                      {effectOf(confirming.change)}
                    </p>
                    <div className="form-actions">
                      <Button variant={'is_active' in confirming.change && !confirming.change.is_active ? 'danger' : 'primary'} onClick={() => void apply(confirming)}>
                        Confirmar
                      </Button>
                      <Button variant="ghost" onClick={() => setPending(null)}>
                        Cancelar
                      </Button>
                    </div>
                  </div>
                )}
                {busy && (
                  <div className="user-row__wide">
                    <Spinner label="Aplicando…" />
                  </div>
                )}
                {rowNotice && (
                  <div className="user-row__wide">
                    <Alert tone={rowNotice.tone}>
                      <p>{rowNotice.text}</p>
                      {rowNotice.check && (
                        <Button variant="secondary" disabled={busy} onClick={() => void check(rowNotice.check!)}>
                          Comprobar estado
                        </Button>
                      )}
                    </Alert>
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
      {result.status === 'ready' && (page > 1 || users.length === ADMIN_USERS_PAGE_SIZE) && (
        <nav className="pagination" aria-label="Paginación">
          {page > 1 ? (
            <Link to={page > 2 ? `/admin/usuarios?pagina=${page - 1}` : '/admin/usuarios'} className="btn btn--secondary" rel="prev">
              Anterior
            </Link>
          ) : (
            <span className="btn btn--secondary pagination__off" aria-disabled="true">
              Anterior
            </span>
          )}
          <span className="pagination__status">Página {page}</span>
          {users.length === ADMIN_USERS_PAGE_SIZE ? (
            <Link to={`/admin/usuarios?pagina=${page + 1}`} className="btn btn--secondary" rel="next">
              Siguiente
            </Link>
          ) : (
            <span className="btn btn--secondary pagination__off" aria-disabled="true">
              Siguiente
            </span>
          )}
        </nav>
      )}
    </section>
  );
}
