// Orders list. A customer sees their own orders; an admin receives the orders of every
// customer from order-service, and the page says so.
import { useEffect, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { listOrders } from '../api/orders';
import type { NumberedPage, OrderOut } from '../api/types';
import { useAuth } from '../auth/AuthContext';
import { readCatalogParams } from '../catalog/params';
import { ErrorAlert } from '../components/Alert';
import { Button } from '../components/Button';
import { EmptyState } from '../components/EmptyState';
import { OrderStatusBadge } from '../components/OrderParts';
import { Pagination } from '../components/Pagination';
import { Spinner } from '../components/Spinner';
import { shortId } from '../orders/orderStatus';
import { formatDateTime, formatMoney } from '../utils/format';

export const ORDERS_PAGE_SIZE = 10;

type Result = { status: 'loading' } | { status: 'ready'; page: NumberedPage<OrderOut> } | { status: 'error'; error: unknown };

export function OrdersPage() {
  const { user } = useAuth();
  const isAdmin = user?.role === 'admin';
  const [search] = useSearchParams();
  const { page } = readCatalogParams(search); // same ?pagina= rules as the catalogue
  const [attempt, setAttempt] = useState(0);
  const [result, setResult] = useState<Result>({ status: 'loading' });

  useEffect(() => {
    const controller = new AbortController();
    setResult({ status: 'loading' });
    listOrders(page, ORDERS_PAGE_SIZE, controller.signal)
      .then((data) => {
        if (!controller.signal.aborted) setResult({ status: 'ready', page: data });
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) setResult({ status: 'error', error });
      });
    return () => controller.abort();
  }, [page, attempt, user?.id]);

  return (
    <section className="orders">
      <header className="page-header">
        <h1>{isAdmin ? 'Pedidos de todos los clientes' : 'Mis pedidos'}</h1>
        <p className="page-header__lead">
          {isAdmin
            ? 'Como administrador ves todos los pedidos. Solo puedes pagar o cancelar los tuyos.'
            : 'Tus compras, de la más reciente a la más antigua.'}
        </p>
      </header>

      {result.status === 'loading' && (
        <div className="catalog__state">
          <Spinner label="Cargando pedidos…" />
        </div>
      )}
      {result.status === 'error' && (
        <div className="catalog__state">
          <ErrorAlert error={result.error} title="No pudimos cargar los pedidos." />
          <Button variant="secondary" onClick={() => setAttempt((n) => n + 1)}>
            Reintentar
          </Button>
        </div>
      )}
      {result.status === 'ready' && result.page.total === 0 && (
        <EmptyState
          title="Todavía no hay pedidos"
          description="Cuando confirmes una compra, aparecerá aquí."
          action={
            <Link to="/productos" className="btn btn--primary">
              Ver catálogo
            </Link>
          }
        />
      )}
      {result.status === 'ready' && result.page.total > 0 && result.page.items.length === 0 && (
        <EmptyState
          title="Esta página no existe"
          action={
            <Link to="/pedidos" className="btn btn--secondary">
              Ir a la primera página
            </Link>
          }
        />
      )}
      {result.status === 'ready' && result.page.items.length > 0 && (
        <>
          <ul className="order-list" aria-label="Pedidos">
            {result.page.items.map((order) => {
              const units = order.items.reduce((sum, item) => sum + item.quantity, 0);
              const own = order.user_id === user?.id;
              return (
                <li key={order.id} className="order-row">
                  <div className="order-row__main">
                    <Link to={`/pedidos/${order.id}`} className="order-row__id">
                      Pedido {shortId(order.id)}
                    </Link>
                    <span className="order-row__meta">
                      {formatDateTime(order.created_at)} · {units} {units === 1 ? 'unidad' : 'unidades'}
                      {isAdmin && <> · {own ? 'Tuyo' : `Cliente #${order.user_id}`}</>}
                    </span>
                  </div>
                  <OrderStatusBadge order={order} />
                  <span className="price">{formatMoney(order.total, order.currency)}</span>
                </li>
              );
            })}
          </ul>
          <Pagination
            page={page}
            totalPages={Math.max(1, Math.ceil(result.page.total / ORDERS_PAGE_SIZE))}
            hrefFor={(target) => (target > 1 ? `/pedidos?pagina=${target}` : '/pedidos')}
          />
        </>
      )}
    </section>
  );
}
