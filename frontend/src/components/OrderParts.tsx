// Small pieces of the order pages: state badge, product lines and expiry countdown.
import { useEffect, useRef } from 'react';
import type { OrderOut } from '../api/types';
import { useCountdown } from '../auth/useCountdown';
import { describeOrder, expiresAtMs } from '../orders/orderStatus';
import { formatMoney } from '../utils/format';
import { StatusBadge } from './StatusBadge';

export function OrderStatusBadge({ order }: { order: OrderOut }) {
  const view = describeOrder(order);
  return <StatusBadge tone={view.tone} label={view.label} />;
}

/** Product lines as saved in the order: prices fixed by order-service at checkout */
export function OrderItemsTable({ order }: { order: OrderOut }) {
  return (
    <div className="order-items">
      <table>
        <thead>
          <tr>
            <th scope="col">Producto</th>
            <th scope="col" className="num">Precio</th>
            <th scope="col" className="num">Cantidad</th>
            <th scope="col" className="num">Subtotal</th>
          </tr>
        </thead>
        <tbody>
          {order.items.map((item) => (
            <tr key={item.product_id}>
              <td>
                <span className="order-items__name">{item.name}</span>
                <span className="product-card__sku">SKU {item.sku}</span>
              </td>
              <td className="num" data-label="Precio">{formatMoney(item.unit_price, order.currency)}</td>
              <td className="num" data-label="Cantidad">{item.quantity}</td>
              <td className="num" data-label="Subtotal">{formatMoney(item.subtotal, order.currency)}</td>
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr>
            <th scope="row" colSpan={3}>Total</th>
            <td className="num price">{formatMoney(order.total, order.currency)}</td>
          </tr>
        </tfoot>
      </table>
    </div>
  );
}

function clock(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}:${String(s).padStart(2, '0')}`;
}

/** Time left to pay. The server decides; the browser clock is only a guide. */
export function ExpiryCountdown({ order, onExpire }: { order: OrderOut; onExpire(): void }) {
  const until = expiresAtMs(order);
  const seconds = useCountdown(until);
  const fired = useRef(false);
  useEffect(() => {
    if (until !== null && seconds === 0 && !fired.current) {
      fired.current = true;
      onExpire();
    }
  }, [until, seconds, onExpire]);
  if (until === null) return null;
  return seconds > 0 ? (
    <p className="order-expiry" aria-live="polite">
      Tienes <strong className="numeric">{clock(seconds)}</strong> para pagar; después la reserva se libera.
    </p>
  ) : (
    <p className="order-expiry order-expiry--over">El tiempo para pagar terminó. El sistema cancelará el pedido en breve.</p>
  );
}
