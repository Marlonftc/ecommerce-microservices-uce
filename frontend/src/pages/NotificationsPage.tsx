// Notifications of the signed-in customer: 20 per page, newest first, optionally only the
// unread ones. Titles and messages come from notification-service and are rendered as
// text by React (never as HTML).
import { useEffect, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { listNotifications, markNotificationRead } from '../api/notifications';
import { isOrderId } from '../api/orders';
import type { NotificationOut, NumberedPage } from '../api/types';
import { readCatalogParams } from '../catalog/params';
import { ErrorAlert } from '../components/Alert';
import { Button } from '../components/Button';
import { EmptyState } from '../components/EmptyState';
import { Pagination } from '../components/Pagination';
import { Spinner } from '../components/Spinner';
import { useNotifications } from '../notifications/NotificationsContext';
import { formatDateTime } from '../utils/format';

export const NOTIFICATIONS_PAGE_SIZE = 20;

type Result =
  | { status: 'loading' }
  | { status: 'ready'; page: NumberedPage<NotificationOut> }
  | { status: 'error'; error: unknown };

function hrefFor(page: number, unreadOnly: boolean): string {
  const search = new URLSearchParams();
  if (unreadOnly) search.set('no_leidas', '1');
  if (page > 1) search.set('pagina', String(page));
  const query = search.toString();
  return query ? `/notificaciones?${query}` : '/notificaciones';
}

export function NotificationsPage() {
  const [search, setSearch] = useSearchParams();
  const { page } = readCatalogParams(search); // same ?pagina= rules as the catalogue
  const unreadOnly = search.get('no_leidas') === '1';
  const [attempt, setAttempt] = useState(0);
  const [result, setResult] = useState<Result>({ status: 'loading' });
  const { refresh } = useNotifications();

  useEffect(() => {
    const controller = new AbortController();
    setResult({ status: 'loading' });
    listNotifications(page, NOTIFICATIONS_PAGE_SIZE, unreadOnly, controller.signal)
      .then((data) => {
        if (!controller.signal.aborted) setResult({ status: 'ready', page: data });
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) setResult({ status: 'error', error });
      });
    return () => controller.abort();
  }, [page, unreadOnly, attempt]);

  /** The marked notification is replaced in place by the answer of the service */
  const replace = (updated: NotificationOut) =>
    setResult((r) =>
      r.status === 'ready'
        ? { ...r, page: { ...r.page, items: r.page.items.map((n) => (n.id === updated.id ? updated : n)) } }
        : r,
    );

  return (
    <section className="notifications">
      <header className="page-header">
        <h1>Notificaciones</h1>
        <p className="page-header__lead">Avisos sobre tus pedidos, del más reciente al más antiguo.</p>
      </header>

      <label className="notifications__filter">
        <input
          type="checkbox"
          checked={unreadOnly}
          onChange={(event) => setSearch(new URLSearchParams(event.target.checked ? { no_leidas: '1' } : {}))}
        />
        Solo no leídas
      </label>

      {result.status === 'loading' && (
        <div className="catalog__state">
          <Spinner label="Cargando notificaciones…" />
        </div>
      )}
      {result.status === 'error' && (
        <div className="catalog__state">
          <ErrorAlert error={result.error} title="No pudimos cargar las notificaciones." />
          <Button variant="secondary" onClick={() => setAttempt((n) => n + 1)}>
            Reintentar
          </Button>
        </div>
      )}
      {result.status === 'ready' && result.page.total === 0 && (
        <EmptyState
          title={unreadOnly ? 'No tienes notificaciones sin leer' : 'Todavía no tienes notificaciones'}
          description={unreadOnly ? undefined : 'Te avisaremos aquí cuando un pedido se pague, se rechace o se cancele.'}
        />
      )}
      {result.status === 'ready' && result.page.total > 0 && result.page.items.length === 0 && (
        <EmptyState
          title="Esta página no existe"
          action={
            <Link to={hrefFor(1, unreadOnly)} className="btn btn--secondary">
              Ir a la primera página
            </Link>
          }
        />
      )}
      {result.status === 'ready' && result.page.items.length > 0 && (
        <>
          <ul className="notification-list" aria-label="Notificaciones">
            {result.page.items.map((notification) => (
              <NotificationItem
                key={notification.id}
                notification={notification}
                onRead={(updated) => {
                  replace(updated);
                  refresh({ force: true });
                }}
              />
            ))}
          </ul>
          <Pagination
            page={page}
            totalPages={Math.max(1, Math.ceil(result.page.total / NOTIFICATIONS_PAGE_SIZE))}
            hrefFor={(target) => hrefFor(target, unreadOnly)}
          />
        </>
      )}
    </section>
  );
}

function NotificationItem({ notification, onRead }: { notification: NotificationOut; onRead(updated: NotificationOut): void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const busyRef = useRef(false);

  const markRead = async () => {
    if (busyRef.current) return; // a double click sends one request
    busyRef.current = true;
    setBusy(true);
    setError(null);
    try {
      onRead(await markNotificationRead(notification.id));
    } catch (failure) {
      setError(failure);
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  };

  return (
    <li className={`notification${notification.is_read ? '' : ' notification--unread'}`}>
      <div className="notification__head">
        <h2 className="notification__title">{notification.title}</h2>
        {!notification.is_read && <span className="notification__new">Nueva</span>}
      </div>
      <p className="notification__message">{notification.message}</p>
      <p className="notification__meta">{formatDateTime(notification.created_at)}</p>
      <div className="notification__actions">
        {isOrderId(notification.order_id) && (
          <Link to={`/pedidos/${notification.order_id}`} className="btn btn--secondary">
            Ver pedido
          </Link>
        )}
        {!notification.is_read && (
          <Button variant="ghost" loading={busy} onClick={() => void markRead()}>
            Marcar como leída
          </Button>
        )}
      </div>
      {error !== null && <ErrorAlert error={error} title="No se pudo marcar como leída." />}
    </li>
  );
}
