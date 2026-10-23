// Checkout: review the cart and create the order. The final prices and the total are
// calculated by order-service; the page shows the estimate of the cart until then.
import { useEffect } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { ApiError } from '../api/errors';
import { useCart } from '../cart/CartContext';
import { CART_MESSAGES } from '../cart/cartErrors';
import { Alert, ErrorAlert } from '../components/Alert';
import { Button } from '../components/Button';
import { Spinner } from '../components/Spinner';
import { useActiveOrder } from '../orders/useActiveOrder';
import { useCheckout, type CheckoutPhase } from '../orders/useCheckout';
import { formatMoney } from '../utils/format';

function ActiveOrderLink() {
  const lookup = useActiveOrder(true);
  if (lookup.status === 'found') {
    return (
      <Link to={`/pedidos/${lookup.order.id}`} className="btn btn--secondary">
        Ver mi compra en curso
      </Link>
    );
  }
  if (lookup.status === 'loading') return <Spinner label="Buscando tu compra en curso…" />;
  return (
    <Link to="/pedidos" className="btn btn--secondary">
      Ver mis pedidos
    </Link>
  );
}

export function CheckoutPage() {
  const cart = useCart();
  const { phase, confirm, retryNow, reset } = useCheckout();
  const navigate = useNavigate();
  const { reload } = cart;

  useEffect(() => {
    void reload();
  }, [reload]);

  useEffect(() => {
    if (phase.name === 'done') navigate(`/pedidos/${phase.orderId}`, { replace: true });
  }, [phase, navigate]);

  const current = cart.cart;
  const lines = current?.items ?? [];
  const unavailable = lines.filter((item) => item.available === false);
  const blocked =
    !current ? 'Cargando tu carrito…'
    : current.locked ? CART_MESSAGES.locked
    : lines.length === 0 ? 'Tu carrito está vacío.'
    : !current.prices_available ? 'No pudimos consultar los precios del catálogo. Inténtalo en unos segundos.'
    : unavailable.length > 0 ? 'Hay productos que ya no están disponibles. Quítalos del carrito para continuar.'
    : cart.needsReload ? 'Estamos confirmando el estado de tu carrito.'
    : null;
  const working = phase.name === 'sending' || phase.name === 'waiting';
  const nameOf = (id: number) => lines.find((item) => item.product_id === id)?.name ?? `Producto #${id}`;

  return (
    <section className="checkout">
      <header className="page-header">
        <h1>Confirmar pedido</h1>
        <p className="page-header__lead">Revisa tu compra. Al confirmar, se reservan las unidades por 15 minutos para que puedas pagar.</p>
      </header>

      <PhaseMessage phase={phase} nameOf={nameOf} onRetry={retryNow} onReset={reset} />

      {!current && cart.status === 'error' ? (
        <div className="catalog__state">
          <ErrorAlert error={cart.loadError} title="No pudimos cargar tu carrito." />
          <Button variant="secondary" onClick={() => void reload()}>
            Reintentar
          </Button>
        </div>
      ) : !current ? (
        <div className="catalog__state">
          <Spinner label="Cargando tu carrito…" />
        </div>
      ) : (
        <div className="cart-layout">
          <ul className="cart-lines" aria-label="Productos a comprar">
            {lines.map((item) => (
              <li key={item.product_id} className="checkout-line">
                <div className="cart-line__product">
                  <span className="cart-line__name">{item.name ?? `Producto #${item.product_id}`}</span>
                  {item.sku && <span className="product-card__sku">SKU {item.sku}</span>}
                </div>
                <span className="numeric">× {item.quantity}</span>
                <span className="cart-line__subtotal">{item.subtotal !== null ? formatMoney(item.subtotal) : '—'}</span>
              </li>
            ))}
          </ul>
          <aside className="cart-summary" aria-label="Resumen">
            <div className="cart-summary__total">
              <span>Total estimado</span>
              <span className={current.estimated_total !== null ? 'price price--large' : 'cart-summary__missing'}>
                {current.estimated_total !== null ? formatMoney(current.estimated_total, current.currency) : 'No disponible'}
              </span>
            </div>
            <p className="cart-summary__note">
              El total definitivo lo calcula el sistema con los precios vigentes al crear el pedido, y lo verás antes de pagar.
            </p>
            {blocked && phase.name === 'idle' && <p className="purchase-box__blocked">{blocked}</p>}
            {current.locked && phase.name === 'idle' && <ActiveOrderLink />}
            <Button onClick={confirm} loading={working} disabled={blocked !== null || working || phase.name === 'stalled'}>
              Confirmar pedido
            </Button>
            <Link to="/carrito" className="btn btn--ghost">
              Volver al carrito
            </Link>
          </aside>
        </div>
      )}
    </section>
  );
}

function PhaseMessage({
  phase, nameOf, onRetry, onReset,
}: { phase: CheckoutPhase; nameOf(id: number): string; onRetry(): void; onReset(): void }) {
  switch (phase.name) {
    case 'sending':
      return (
        <Alert tone="info">
          <p>{phase.recovering ? 'Comprobando tu intento de compra anterior…' : 'Creando tu pedido…'}</p>
        </Alert>
      );
    case 'waiting':
      return (
        <Alert tone="info" title={phase.reason === 'reserving' ? 'Reservando tus productos' : 'Comprobando tu pedido'}>
          <p>
            {phase.reason === 'reserving'
              ? 'El inventario todavía no confirmó la reserva. Lo intentamos de nuevo automáticamente.'
              : 'No recibimos la respuesta del servidor. Comprobamos tu pedido con la misma operación, sin crear otro.'}
          </p>
        </Alert>
      );
    case 'stalled':
      return (
        <Alert tone="warning" title={phase.reason === 'reserving' ? 'El inventario no responde' : 'No pudimos confirmar tu pedido'}>
          <p>
            {phase.reason === 'reserving'
              ? 'Tu pedido está guardado, pero las unidades aún no se reservaron. Si no se confirma en unos minutos, el sistema lo cancelará solo.'
              : 'Todavía no sabemos si el pedido se creó. Reintentar usa la misma operación: nunca crea un pedido duplicado.'}
          </p>
          <div className="form-actions">
            <Button variant="secondary" onClick={onRetry}>
              Reintentar
            </Button>
            {phase.orderId && (
              <Link to={`/pedidos/${phase.orderId}`} className="btn btn--ghost">
                Ver el pedido
              </Link>
            )}
          </div>
        </Alert>
      );
    case 'failed': {
      const { problem, productIds, orderId } = phase;
      return (
        <Alert tone={problem.kind === 'business' ? 'warning' : 'error'} requestId={problem.error instanceof ApiError ? problem.error.requestId : null}>
          <p>{problem.message}</p>
          {productIds.length > 0 && <p>Productos: {productIds.map(nameOf).join(', ')}.</p>}
          <div className="form-actions">
            {problem.code === 'CART_LOCKED' ? <ActiveOrderLink /> : null}
            {orderId && (
              <Link to={`/pedidos/${orderId}`} className="btn btn--ghost">
                Ver el pedido cancelado
              </Link>
            )}
            {problem.code !== 'CART_LOCKED' && problem.code !== null && (
              <Link to="/carrito" className="btn btn--secondary">
                Revisar el carrito
              </Link>
            )}
            {problem.kind !== 'business' && (
              <Button variant="secondary" onClick={onReset}>
                Entendido
              </Button>
            )}
          </div>
        </Alert>
      );
    }
    default:
      return null;
  }
}
