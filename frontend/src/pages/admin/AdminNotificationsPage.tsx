// Notifications of every customer, for an admin, read-only: notification-service returns all of
// them to an admin but only lets each user mark their own, so there is no "mark as read" here.
// "Read" is the customer's state. Customers' names come from ONE GET /api/users?ids= per page
// (useBuyers, as in the orders list); if user-service fails, "Cliente #ID" is shown.
import { useEffect, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { listNotifications } from '../../api/notifications';
import { isOrderId } from '../../api/orders';
import type { NotificationOut, NumberedPage } from '../../api/types';
import { useAuth } from '../../auth/AuthContext';
import { useForbiddenCheck } from '../../admin/adminSession';
import { readCatalogParams } from '../../catalog/params';
import { Alert, ErrorAlert } from '../../components/Alert';
import { Button } from '../../components/Button';
import { EmptyState } from '../../components/EmptyState';
import { Pagination } from '../../components/Pagination';
import { Spinner } from '../../components/Spinner';
import { buyerName, useBuyers } from '../../orders/useBuyers';
import { formatDateTime } from '../../utils/format';

export const ADMIN_NOTIFICATIONS_PAGE_SIZE = 20;

type Result = { status: 'loading' } | { status: 'ready'; page: NumberedPage<NotificationOut> } | { status: 'error'; error: unknown };

function hrefFor(page: number, unreadOnly: boolean): string {
  const search = new URLSearchParams();
  if (unreadOnly) search.set('no_leidas', '1');
  if (page > 1) search.set('pagina', String(page));
  const query = search.toString();
  return query ? `/admin/notificaciones?${query}` : '/admin/notificaciones';
}

export function AdminNotificationsPage() {
  const { user } = useAuth();
  const onForbidden = useForbiddenCheck();
  const [search, setSearch] = useSearchParams();
  const { page } = readCatalogParams(search);
  const unreadOnly = search.get('no_leidas') === '1';
  const [attempt, setAttempt] = useState(0);
  const [result, setResult] = useState<Result>({ status: 'loading' });

  useEffect(() => {
    const controller = new AbortController();
    setResult({ status: 'loading' });
    listNotifications(page, ADMIN_NOTIFICATIONS_PAGE_SIZE, unreadOnly, controller.signal)
      .then((data) => {
        if (!controller.signal.aborted) setResult({ status: 'ready', page: data });
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        if (!onForbidden(error)) setResult({ status: 'error', error });
      });
    return () => controller.abort();
  }, [page, unreadOnly, attempt, onForbidden]);

  const items = result.status === 'ready' ? result.page.items : [];
  const buyers = useBuyers(items.map((n) => n.user_id).filter((id) => id !== user?.id), user?.role === 'admin');

  return (
    <section className="notifications admin-notifications">
      <nav className="breadcrumb" aria-label="Ruta">
        <Link to="/admin">Administración</Link>
        <span aria-hidden="true">/</span>
        <span>Notificaciones de clientes</span>
      </nav>
      <header className="page-header">
        <h1>Notificaciones de clientes</h1>
        <p className="page-header__lead">Solo lectura. «Leída» indica si el cliente la abrió; el administrador no puede marcarlas.</p>
      </header>

      <label className="notifications__filter">
        <input
          type="checkbox"
          checked={unreadOnly}
          onChange={(event) => setSearch(new URLSearchParams(event.target.checked ? { no_leidas: '1' } : {}))}
        />
        Solo las no leídas por el cliente
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
        <EmptyState title={unreadOnly ? 'No hay notificaciones sin leer' : 'Todavía no hay notificaciones'} />
      )}
      {result.status === 'ready' && result.page.total > 0 && items.length === 0 && (
        <EmptyState
          title="Esta página no existe"
          action={
            <Link to={hrefFor(1, unreadOnly)} className="btn btn--secondary">
              Ir a la primera página
            </Link>
          }
        />
      )}
      {result.status === 'ready' && items.length > 0 && (
        <>
          {buyers.status === 'error' && (
            <Alert tone="info">
              <p>No pudimos obtener los nombres de los clientes; se muestra su número.</p>
            </Alert>
          )}
          <ul className="notification-list" aria-label="Notificaciones de clientes">
            {items.map((n) => {
              const buyer = buyers.byId.get(n.user_id);
              return (
                <li key={n.id} className={`notification${n.is_read ? '' : ' notification--unread'}`}>
                  <div className="notification__head">
                    <h2 className="notification__title">{n.title}</h2>
                    <span className={n.is_read ? 'notification__read' : 'notification__new'}>
                      {n.is_read ? `Leída por el cliente${n.read_at ? ` el ${formatDateTime(n.read_at)}` : ''}` : 'No leída por el cliente'}
                    </span>
                  </div>
                  <p className="notification__message">{n.message}</p>
                  <p className="notification__meta">
                    {n.user_id === user?.id ? 'Tu cuenta' : buyerName(n.user_id, buyers)}
                    {buyer && <> · {buyer.email}</>} · {formatDateTime(n.created_at)}
                    {isOrderId(n.order_id) && (
                      <>
                        {' · '}
                        <Link to={`/pedidos/${n.order_id}`}>Ver pedido</Link>
                      </>
                    )}
                  </p>
                </li>
              );
            })}
          </ul>
          <Pagination
            page={page}
            totalPages={Math.max(1, Math.ceil(result.page.total / ADMIN_NOTIFICATIONS_PAGE_SIZE))}
            hrefFor={(target) => hrefFor(target, unreadOnly)}
          />
        </>
      )}
    </section>
  );
}
