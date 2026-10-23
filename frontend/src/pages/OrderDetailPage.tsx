// One order: state, products with the prices fixed at checkout, simulated payment and
// cancellation. Only the owner pays or cancels; an admin sees other orders read-only.
import { useCallback, useEffect, useRef, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { ApiError } from '../api/errors';
import { cancelOrder, isOrderId, payOrder } from '../api/orders';
import type { OrderOut, Simulation } from '../api/types';
import { useAuth } from '../auth/AuthContext';
import { useCart } from '../cart/CartContext';
import { paymentDemoEnabled } from '../config';
import { Alert, ErrorAlert } from '../components/Alert';
import { Button } from '../components/Button';
import { ExpiryCountdown, OrderItemsTable, OrderStatusBadge } from '../components/OrderParts';
import { Spinner } from '../components/Spinner';
import { useNotifications } from '../notifications/NotificationsContext';
import { classifyPaymentError, extraString } from '../orders/orderErrors';
import { SIMULATION_LABELS, canCancel, canPay, describeOrder, isOwner, releaseText, shortId } from '../orders/orderStatus';
import { clearPayment, getPayment, newKey, savePayment, type PendingPayment } from '../orders/pendingOps';
import { useBuyers } from '../orders/useBuyers';
import { useOrderTracker, type OrderTracker } from '../orders/useOrderTracker';
import { formatDateTime, formatMoney } from '../utils/format';
import { NotFoundPage } from './NotFoundPage';

const FINAL = new Set(['PAID', 'PAYMENT_REJECTED', 'CANCELLED']);

export function OrderDetailPage() {
  const { orderId } = useParams();
  if (!isOrderId(orderId)) return <NotFoundPage />;
  return <OrderDetail key={orderId} orderId={orderId} />;
}

interface Notice {
  tone: 'success' | 'info' | 'warning' | 'error';
  text: string;
}

function OrderDetail({ orderId }: { orderId: string }) {
  const tracker = useOrderTracker(orderId);
  const { user } = useAuth();
  const { reload: reloadCart } = useCart();
  const { refresh: refreshNotifications } = useNotifications();
  const order = tracker.order;
  const owner = order !== null && isOwner(order, user?.id);
  // Name and email of the customer, only for an admin looking at someone else's order
  const buyers = useBuyers(order && !owner ? [order.user_id] : [], user?.role === 'admin');

  // The cart changes on the server when an order ends (emptied when PAID, unlocked otherwise),
  // and order-service sends the notification of the result in the same request
  const lastStatus = useRef<string | null>(null);
  useEffect(() => {
    if (!order) return;
    if (lastStatus.current !== null && lastStatus.current !== order.status && FINAL.has(order.status)) {
      void reloadCart();
      refreshNotifications({ force: true });
    }
    lastStatus.current = order.status;
  }, [order, reloadCart, refreshNotifications]);

  if (tracker.status === 'loading') {
    return (
      <div className="catalog__state">
        <Spinner label="Cargando el pedido…" />
      </div>
    );
  }
  if (!order) {
    const notFound = tracker.error instanceof ApiError && tracker.error.status === 404;
    return notFound ? (
      <section className="not-found">
        <h1>Pedido no encontrado</h1>
        <p>El pedido no existe o no pertenece a tu cuenta.</p>
        <Link to="/pedidos" className="btn btn--secondary">
          Ver mis pedidos
        </Link>
      </section>
    ) : (
      <section className="catalog__state">
        <ErrorAlert error={tracker.error} title="No pudimos cargar el pedido." />
        <Button variant="secondary" onClick={() => void tracker.refresh()}>
          Reintentar
        </Button>
      </section>
    );
  }

  const view = describeOrder(order, owner);
  const buyer = buyers.byId.get(order.user_id);

  return (
    <article className="order-detail">
      <nav className="breadcrumb" aria-label="Ruta">
        <Link to="/pedidos">Pedidos</Link>
        <span aria-hidden="true">/</span>
        <span>Pedido {shortId(order.id)}</span>
      </nav>
      <header className="page-header page-header--row">
        <div>
          <h1>Pedido {shortId(order.id)}</h1>
          <p className="page-header__lead">Creado el {formatDateTime(order.created_at)}</p>
        </div>
        <OrderStatusBadge order={order} />
      </header>

      {!owner && (
        <Alert tone="info">
          <p>
            {buyer ? (
              <>
                Pedido de <strong>{buyer.full_name}</strong> ({buyer.email}).
              </>
            ) : (
              <>Pedido del cliente #{order.user_id}.</>
            )}{' '}
            Como administrador puedes consultarlo, pero solo su dueño puede pagarlo o cancelarlo.
          </p>
        </Alert>
      )}

      <div className="order-detail__layout">
        <div>
          <p className="order-detail__state">{view.detail}</p>
          {order.status === 'PENDING_PAYMENT' && <ExpiryCountdown order={order} onExpire={() => void tracker.refresh()} />}
          {tracker.polling && <p className="order-detail__live">Actualizando automáticamente…</p>}
          {tracker.pollingStopped && (
            <Alert tone="info">
              <p>El pedido todavía se está procesando. Puedes volver más tarde o actualizarlo ahora.</p>
              <Button variant="secondary" onClick={() => void tracker.refresh()}>
                Actualizar
              </Button>
            </Alert>
          )}
          <OrderItemsTable order={order} />
          <p className="cart-summary__note">Precios fijados por el sistema al crear el pedido.</p>
        </div>
        {owner && <OrderActions order={order} tracker={tracker} userId={user!.id} />}
      </div>
    </article>
  );
}

function OrderActions({ order, tracker, userId }: { order: OrderOut; tracker: OrderTracker; userId: number }) {
  const [pending, setPending] = useState<PendingPayment | null>(() => getPayment(userId, order.id));
  const [simulation, setSimulation] = useState<Simulation>(pending?.simulation ?? 'APPROVE');
  const [notice, setNotice] = useState<Notice | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirmCancel, setConfirmCancel] = useState(false);
  const busyRef = useRef(false);
  const demo = paymentDemoEnabled();

  // A final order needs no saved payment attempt any more
  useEffect(() => {
    if (FINAL.has(order.status) && getPayment(userId, order.id)) {
      clearPayment(userId, order.id);
      setPending(null);
    }
  }, [order.status, order.id, userId]);

  // A payment resolved in the background (worker): replace the "confirming" message
  const previous = useRef(order.status);
  useEffect(() => {
    if (previous.current === 'PAYMENT_PENDING' && order.status === 'PAID') {
      setNotice({ tone: 'success', text: 'Pago aprobado. Tu carrito se vació.' });
    } else if (previous.current === 'PAYMENT_PENDING' && order.status === 'PAYMENT_REJECTED') {
      setNotice({ tone: 'warning', text: 'El pago fue rechazado. Tu carrito se conservó: puedes intentarlo con un pedido nuevo.' });
    }
    previous.current = order.status;
  }, [order.status]);

  const run = useCallback(async (task: () => Promise<void>) => {
    if (busyRef.current) return; // one action at a time, not only a disabled button
    busyRef.current = true;
    setBusy(true);
    try {
      await task();
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  }, []);

  const pay = () =>
    run(async () => {
      setNotice(null);
      // The same attempt (key and mode) is reused until its result is known
      // Without the demonstration options the only mode is APPROVE, always sent explicitly
      const chosen: Simulation = demo ? simulation : 'APPROVE';
      const attempt = getPayment(userId, order.id) ?? savePayment({ userId, orderId: order.id, key: newKey(), simulation: chosen });
      setPending(attempt);
      try {
        const { order: updated, status } = await payOrder(order.id, attempt.simulation, attempt.key);
        tracker.update(updated);
        if (status === 202 || updated.status === 'PAYMENT_PENDING') {
          setNotice({ tone: 'info', text: 'Estamos confirmando tu pago. No vuelvas a pagar: el resultado aparecerá aquí.' });
          return;
        }
        clearPayment(userId, order.id);
        setPending(null);
        setNotice(
          updated.status === 'PAID'
            ? { tone: 'success', text: 'Pago aprobado. Tu carrito se vació.' }
            : { tone: 'warning', text: 'El pago fue rechazado. Tu carrito se conservó: puedes intentarlo con un pedido nuevo.' },
        );
      } catch (error) {
        const problem = classifyPaymentError(error);
        if (problem.kind === 'uncertain') {
          // Find out what happened before allowing anything else; the attempt stays saved
          const current = await tracker.refresh();
          if (current?.status === 'PENDING_PAYMENT') {
            setNotice({ tone: 'warning', text: 'No sabemos si el pago llegó. Reintentar repite la misma operación: nunca cobra dos veces.' });
          } else if (current?.status === 'PAYMENT_PENDING') {
            setNotice({ tone: 'info', text: 'Tu pago se está procesando. El resultado aparecerá aquí.' });
          } else if (!current) {
            setNotice({ tone: 'warning', text: 'No pudimos comprobar el pago. Actualiza el pedido antes de intentar otra vez.' });
          }
          return;
        }
        if (problem.code === 'ORDER_NOT_PAYABLE' || problem.code === 'IDEMPOTENCY_KEY_REUSED') {
          clearPayment(userId, order.id);
          setPending(null);
          void tracker.refresh();
          const expired = extraString(error, 'status') === 'EXPIRED';
          setNotice({ tone: 'warning', text: expired ? 'El tiempo para pagar este pedido terminó.' : problem.message });
          return;
        }
        setNotice({ tone: 'error', text: problem.message });
      }
    });

  const cancel = () =>
    run(async () => {
      setConfirmCancel(false);
      setNotice(null);
      try {
        const updated = await cancelOrder(order.id);
        tracker.update(updated);
        setNotice({ tone: 'success', text: `Pedido cancelado. ${releaseText(updated)}` });
      } catch (error) {
        void tracker.refresh();
        const text =
          error instanceof ApiError && error.code === 'ORDER_NOT_CANCELLABLE' ? 'Este pedido ya no se puede cancelar.'
          : error instanceof ApiError ? error.message
          : 'No se pudo cancelar el pedido.';
        setNotice({ tone: 'error', text });
      }
    });

  const payable = canPay(order, userId);
  const retrying = pending !== null && order.status === 'PENDING_PAYMENT';

  return (
    <aside className="order-actions" aria-label="Acciones del pedido">
      {notice && (
        <Alert tone={notice.tone}>
          <p>{notice.text}</p>
        </Alert>
      )}

      {order.status === 'PENDING_PAYMENT' && (
        <section className="payment-panel" aria-labelledby="payment-title">
          <h2 id="payment-title">Pago</h2>
          <p className="payment-panel__disclaimer">
            Pago simulado: este proyecto no procesa dinero real ni usa tarjetas. El pago lo registra el microservicio de
            pagos.
          </p>
          {retrying && pending && (
            <p className="purchase-box__blocked">
              Hay un intento de pago sin confirmar. Se repetirá con la misma operación
              {pending.simulation !== 'APPROVE' ? ` (${SIMULATION_LABELS[pending.simulation].toLowerCase()})` : ''}; nunca se cobra dos veces.
            </p>
          )}
          {demo && (
            <details className="payment-demo">
              <summary>Opciones de demostración</summary>
              <p className="payment-demo__note">
                Solo para demostrar el sistema: elige el resultado que devolverá el pago simulado. Una vez iniciado el
                pago, la opción no se puede cambiar.
              </p>
              <fieldset className="payment-panel__options" disabled={busy || !payable || retrying}>
                <legend className="visually-hidden">Resultado del pago simulado</legend>
                {(Object.keys(SIMULATION_LABELS) as Simulation[]).map((option) => (
                  <label key={option} className="payment-option">
                    <input
                      type="radio"
                      name="simulation"
                      value={option}
                      checked={(pending?.simulation ?? simulation) === option}
                      onChange={() => setSimulation(option)}
                    />
                    <span>{SIMULATION_LABELS[option]}</span>
                  </label>
                ))}
              </fieldset>
            </details>
          )}
          <Button onClick={() => void pay()} loading={busy} disabled={!payable}>
            {retrying ? 'Reintentar pago' : `Pagar ${formatMoney(order.total, order.currency)}`}
          </Button>
        </section>
      )}

      {canCancel(order, userId) && (
        <div className="order-actions__cancel">
          {confirmCancel ? (
            <div className="cart-summary__confirm" role="group" aria-label="Confirmar cancelación">
              <p>¿Cancelar este pedido? Las unidades reservadas se liberarán.</p>
              <div className="form-actions">
                <Button variant="danger" onClick={() => void cancel()} disabled={busy}>
                  Sí, cancelar
                </Button>
                <Button variant="ghost" onClick={() => setConfirmCancel(false)}>
                  No
                </Button>
              </div>
            </div>
          ) : (
            <Button variant="ghost" onClick={() => setConfirmCancel(true)} disabled={busy}>
              Cancelar pedido
            </Button>
          )}
        </div>
      )}

      {order.status === 'PAYMENT_REJECTED' && (
        <Link to="/carrito" className="btn btn--secondary">
          Volver al carrito
        </Link>
      )}
      {order.status === 'PAID' && (
        <Link to="/productos" className="btn btn--secondary">
          Seguir comprando
        </Link>
      )}
    </aside>
  );
}
