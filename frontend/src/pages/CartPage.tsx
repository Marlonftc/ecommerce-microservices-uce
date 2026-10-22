// Cart page. Amounts (subtotals, estimated total) are always the ones calculated by
// order-service; nothing is added up here. Checkout belongs to stage 4.
import { useEffect, useState, type FormEvent } from 'react';
import { Link } from 'react-router-dom';
import type { CartItemOut } from '../api/types';
import { useCart, type CartResult } from '../cart/CartContext';
import { CART_MESSAGES, describeProblem, isSilent } from '../cart/cartErrors';
import { MAX_QUANTITY, parseQuantity } from '../cart/quantity';
import { Alert, ErrorAlert } from '../components/Alert';
import { Button } from '../components/Button';
import { EmptyState } from '../components/EmptyState';
import { QuantityInput } from '../components/QuantityInput';
import { Spinner } from '../components/Spinner';
import { StatusBadge } from '../components/StatusBadge';
import { formatMoney } from '../utils/format';

interface Notice {
  tone: 'success' | 'warning' | 'error';
  text: string;
}

export function CartPage() {
  const cart = useCart();
  const { reload } = cart;
  const [notice, setNotice] = useState<Notice | null>(null);
  const [confirmClear, setConfirmClear] = useState(false);

  // Always show the cart as order-service has it now, also when coming back to the tab
  useEffect(() => {
    void reload();
    const onVisible = () => {
      if (document.visibilityState === 'visible') void reload();
    };
    document.addEventListener('visibilitychange', onVisible);
    return () => document.removeEventListener('visibilitychange', onVisible);
  }, [reload]);

  function report(result: CartResult, success: string) {
    if (result.ok) {
      setNotice({ tone: 'success', text: success });
      return;
    }
    if (isSilent(result.problem)) return;
    const warning = ['uncertain', 'changed', 'locked', 'needs-reload'].includes(result.problem.kind);
    setNotice({ tone: warning ? 'warning' : 'error', text: describeProblem(result.problem, result.cart !== null) });
  }

  if (!cart.cart) {
    return (
      <section className="cart-page">
        <h1>Tu carrito</h1>
        {cart.status === 'error' ? (
          <div className="catalog__state">
            <ErrorAlert error={cart.loadError} title="No pudimos cargar tu carrito." />
            <Button variant="secondary" onClick={() => void reload()}>
              Reintentar
            </Button>
          </div>
        ) : (
          <div className="catalog__state">
            <Spinner label="Cargando tu carrito…" />
          </div>
        )}
      </section>
    );
  }

  const current = cart.cart;
  const locked = current.locked;
  const frozen = locked || cart.busy || cart.needsReload;
  const hasUnavailable = current.items.some((item) => item.available === false);
  const lines = current.items.length;

  const retryButton = (
    <Button variant="secondary" onClick={() => void reload()} loading={cart.refreshing}>
      Reintentar
    </Button>
  );

  return (
    <section className="cart-page">
      <header className="page-header page-header--row">
        <h1>Tu carrito</h1>
        {lines > 0 && (
          <p className="page-header__lead">
            {cart.itemCount} {cart.itemCount === 1 ? 'unidad' : 'unidades'} de {lines} {lines === 1 ? 'producto' : 'productos'}
          </p>
        )}
      </header>

      <div className="cart-page__alerts">
        {locked && (
          <Alert tone="warning" title="Compra en curso">
            <p>{CART_MESSAGES.locked}</p>
          </Alert>
        )}
        {cart.needsReload && (
          <Alert tone="warning" title="Confirmando tu carrito">
            <p>{cart.refreshing ? 'Consultando tu carrito…' : CART_MESSAGES['needs-reload']}</p>
            {!cart.refreshing && retryButton}
          </Alert>
        )}
        {!cart.needsReload && cart.loadError !== null && (
          <Alert tone="warning">
            <p>No pudimos actualizar tu carrito. Se muestra la última información confirmada.</p>
            {retryButton}
          </Alert>
        )}
        {!current.prices_available && lines > 0 && (
          <Alert tone="warning" title="Precios no disponibles">
            <p>
              No pudimos consultar los precios del catálogo. Tus productos y cantidades siguen guardados; puedes eliminarlos,
              pero las cantidades se podrán cambiar cuando el catálogo responda.
            </p>
            {retryButton}
          </Alert>
        )}
        {notice && (
          <Alert tone={notice.tone}>
            <p>{notice.text}</p>
          </Alert>
        )}
      </div>

      {lines === 0 ? (
        <EmptyState
          title="Tu carrito está vacío"
          description="Explora el catálogo y agrega los productos que quieras comprar."
          action={
            <Link to="/productos" className="btn btn--primary">
              Ver catálogo
            </Link>
          }
        />
      ) : (
        <div className="cart-layout">
          <ul className="cart-lines" aria-label="Productos del carrito">
            {current.items.map((item) => (
              <CartLine
                key={item.product_id}
                item={item}
                pricesAvailable={current.prices_available}
                frozen={frozen}
                onSetQuantity={async (quantity) => {
                  setNotice(null);
                  report(await cart.setQuantity(item.product_id, quantity), 'Cantidad actualizada.');
                }}
                onRemove={async () => {
                  setNotice(null);
                  report(await cart.removeItem(item.product_id), 'Producto eliminado del carrito.');
                }}
              />
            ))}
          </ul>

          <aside className="cart-summary" aria-label="Resumen del carrito">
            <div className="cart-summary__total">
              <span>Total estimado</span>
              <span className={current.estimated_total !== null ? 'price price--large' : 'cart-summary__missing'}>
                {current.estimated_total !== null ? formatMoney(current.estimated_total, current.currency) : 'No disponible por ahora'}
              </span>
            </div>
            {hasUnavailable && <p className="cart-summary__note">Los productos que ya no están disponibles no se incluyen en el total.</p>}
            <p className="cart-summary__note">
              Se calcula con los precios actuales del catálogo. El precio final se confirma al crear el pedido.
            </p>
            <Link to="/productos" className="btn btn--secondary">
              Seguir comprando
            </Link>
            {confirmClear ? (
              <div className="cart-summary__confirm" role="group" aria-label="Confirmar vaciado del carrito">
                <p>¿Quitar {lines === 1 ? 'el producto' : `los ${lines} productos`} del carrito?</p>
                <div className="form-actions">
                  <Button
                    variant="danger"
                    disabled={frozen}
                    onClick={async () => {
                      setConfirmClear(false);
                      setNotice(null);
                      report(await cart.clear(), 'Vaciaste tu carrito.');
                    }}
                  >
                    Sí, vaciar
                  </Button>
                  <Button variant="ghost" onClick={() => setConfirmClear(false)}>
                    Cancelar
                  </Button>
                </div>
              </div>
            ) : (
              <Button variant="ghost" disabled={frozen} onClick={() => setConfirmClear(true)}>
                Vaciar carrito
              </Button>
            )}
          </aside>
        </div>
      )}
    </section>
  );
}

interface CartLineProps {
  item: CartItemOut;
  pricesAvailable: boolean;
  /** Locked cart, a change running or an uncertain outcome: nothing can be changed */
  frozen: boolean;
  onSetQuantity(quantity: number): Promise<void>;
  onRemove(): Promise<void>;
}

function CartLine({ item, pricesAvailable, frozen, onSetQuantity, onRemove }: CartLineProps) {
  const [draft, setDraft] = useState(String(item.quantity));
  useEffect(() => setDraft(String(item.quantity)), [item.quantity]);

  const name = item.name ?? `Producto #${item.product_id}`;
  // PUT needs product-service: without prices, or for a deactivated product, it would fail
  const editable = pricesAvailable && item.available !== false;
  const check = parseQuantity(draft, MAX_QUANTITY);
  const changed = check.ok && check.value !== item.quantity;

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (check.ok && changed) void onSetQuantity(check.value);
  }

  return (
    <li className={`cart-line${item.available === false ? ' cart-line--unavailable' : ''}`}>
      <div className="cart-line__product">
        <Link to={`/productos/${item.product_id}`} className="cart-line__name">
          {name}
        </Link>
        {item.sku && <span className="product-card__sku">SKU {item.sku}</span>}
        {item.available === false && <StatusBadge tone="danger" label="Ya no está disponible" />}
      </div>
      <div className="cart-line__cell">
        <span className="cart-line__label">Precio</span>
        <span>{item.unit_price !== null ? formatMoney(item.unit_price) : 'No disponible'}</span>
      </div>
      <div className="cart-line__cell cart-line__quantity">
        {editable ? (
          <form onSubmit={onSubmit} noValidate>
            <QuantityInput
              id={`qty-${item.product_id}`}
              label={`Cantidad de ${name}`}
              hideLabel
              value={draft}
              onChange={setDraft}
              max={MAX_QUANTITY}
              disabled={frozen}
              error={!check.ok ? check.message : undefined}
            />
            {changed && (
              <Button type="submit" variant="secondary" disabled={frozen}>
                Actualizar
              </Button>
            )}
          </form>
        ) : (
          <>
            <span className="cart-line__label">Cantidad</span>
            <span>{item.quantity}</span>
          </>
        )}
      </div>
      <div className="cart-line__cell">
        <span className="cart-line__label">Subtotal</span>
        <span className="cart-line__subtotal">{item.subtotal !== null ? formatMoney(item.subtotal) : '—'}</span>
      </div>
      <Button variant="ghost" disabled={frozen} onClick={() => void onRemove()} aria-label={`Eliminar ${name}`}>
        Eliminar
      </Button>
    </li>
  );
}
