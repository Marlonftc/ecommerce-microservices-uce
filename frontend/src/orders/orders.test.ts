// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { ApiError } from '../api/errors';
import { order, OTHER_ORDER_ID } from '../test/orders';
import { classifyCheckoutError, classifyPaymentError, extraNumbers, extraString } from './orderErrors';
import {
  STATUS_LABELS, canCancel, canPay, describeOrder, isActive, isSettling, shortId,
} from './orderStatus';
import {
  PENDING_KEY, clearCheckout, clearPayment, getCheckout, getPayment, newKey, saveCheckout, savePayment,
} from './pendingOps';
import { findOwnActiveOrder } from './useActiveOrder';

const http = (status: number, body: unknown) => ApiError.fromResponse(status, body, 'req-1', null);

describe('order states', () => {
  it('uses exactly the six states of order-service; EXPIRED is only a cancel reason', () => {
    expect(Object.keys(STATUS_LABELS).sort()).toEqual(
      ['CANCELLED', 'PAID', 'PAYMENT_PENDING', 'PAYMENT_REJECTED', 'PENDING_PAYMENT', 'RESERVING'],
    );
    const expired = describeOrder(order({ status: 'CANCELLED', cancel_reason: 'EXPIRED', inventory_status: 'RELEASED' }));
    expect(expired.label).toBe('Cancelado');
    expect(expired.detail).toBe('Venció el tiempo para pagar. Las unidades reservadas se liberaron.');
  });

  it('PAID with inventory RESERVED is not a completed purchase yet', () => {
    expect(describeOrder(order({ status: 'PAID', inventory_status: 'RESERVED' })).detail).toMatch(/Falta confirmar las unidades/);
    expect(describeOrder(order({ status: 'PAID', inventory_status: 'CONFIRMED' })).detail).toBe('Compra completada.');
  });

  it('never claims a release that is still pending', () => {
    const pending = describeOrder(order({ status: 'CANCELLED', cancel_reason: 'USER', inventory_status: 'RESERVED' }));
    expect(pending.detail).toContain('está pendiente');
    expect(pending.detail).not.toContain('se liberaron');
    expect(describeOrder(order({ status: 'PAYMENT_REJECTED', inventory_status: 'RESERVED' })).detail).toContain('está pendiente');
    expect(describeOrder(order({ status: 'CANCELLED', cancel_reason: 'INSUFFICIENT_STOCK', inventory_status: 'NOT_RESERVED' })).detail)
      .toContain('No llegaron a reservarse');
  });

  it('PAYMENT_PENDING says the result is unknown and not to pay again', () => {
    expect(describeOrder(order({ status: 'PAYMENT_PENDING' })).detail).toMatch(/aún no se conoce. No vuelvas a pagar/);
  });

  it('knows when the order still changes by itself', () => {
    expect(isSettling(order({ status: 'PAYMENT_PENDING' }))).toBe(true);
    expect(isSettling(order({ status: 'RESERVING' }))).toBe(true);
    expect(isSettling(order({ status: 'PAID', inventory_status: 'RESERVED' }))).toBe(true);
    expect(isSettling(order({ status: 'PAID', inventory_status: 'CONFIRMED' }))).toBe(false);
    expect(isSettling(order({ status: 'CANCELLED', inventory_status: 'RESERVED' }))).toBe(true);
    expect(isSettling(order({ status: 'CANCELLED', inventory_status: 'RELEASED' }))).toBe(false);
    expect(isSettling(order())).toBe(false); // PENDING_PAYMENT before its expiry: the user acts
    expect(isSettling(order({ expires_at: new Date(Date.now() - 1000).toISOString() }))).toBe(true);
  });

  it('only the owner pays (before the expiry) or cancels', () => {
    expect(canPay(order(), 7)).toBe(true);
    expect(canPay(order(), 1)).toBe(false); // an admin, not the owner
    expect(canPay(order({ expires_at: new Date(Date.now() - 1000).toISOString() }), 7)).toBe(false);
    expect(canPay(order({ status: 'PAYMENT_PENDING' }), 7)).toBe(false);
    expect(canCancel(order(), 7)).toBe(true);
    expect(canCancel(order(), 1)).toBe(false);
    expect(canCancel(order({ status: 'PAID' }), 7)).toBe(false);
    expect(isActive(order({ status: 'RESERVING' }))).toBe(true);
    expect(isActive(order({ status: 'PAID' }))).toBe(false);
    expect(shortId('0b9a6c1e-4f2d-4c8a-9e3b-7d5f1a2b3c4d')).toBe('0B9A6C1E');
  });

  it('the active order of an admin is never another customer’s', () => {
    const list = [
      order({ id: OTHER_ORDER_ID, user_id: 8, status: 'PENDING_PAYMENT' }),
      order({ user_id: 1, status: 'PAYMENT_PENDING' }),
    ];
    expect(findOwnActiveOrder(list, 1)?.user_id).toBe(1);
    expect(findOwnActiveOrder(list, 9)).toBeNull();
  });
});

describe('checkout and payment errors', () => {
  it('a 503 of order-service at checkout is safe: no order was created', () => {
    const p = classifyCheckoutError(http(503, { detail: 'product-service is not available, try again later' }));
    expect(p.kind).toBe('unavailable');
    expect(p.message).toContain('No se creó ningún pedido');
  });

  it('timeouts, network errors and gateway errors are uncertain, never "not processed"', () => {
    for (const error of [
      ApiError.timeout(),
      ApiError.network(),
      http(502, { detail: { code: 'UPSTREAM_UNAVAILABLE', message: 'x' } }),
      http(504, { detail: { code: 'UPSTREAM_TIMEOUT', message: 'x' } }),
      http(502, { detail: 'inventory-service has a different reservation for this order' }), // order may exist
      http(500, 'Internal Server Error'),
    ]) {
      expect(classifyCheckoutError(error).kind).toBe('uncertain');
      expect(classifyPaymentError(error).kind).toBe('uncertain');
    }
    expect(classifyPaymentError(http(503, { detail: 'x' })).kind).toBe('uncertain');
    // An error generated by the gateway never means "not processed", whatever its status
    expect(classifyCheckoutError(http(503, { detail: { code: 'UPSTREAM_UNAVAILABLE', message: 'x' } })).kind).toBe('uncertain');
  });

  it('409 business codes get their own message', () => {
    const changed = classifyCheckoutError(http(409, { detail: { code: 'CART_CHANGED', message: 'x' } }));
    expect([changed.kind, changed.code]).toEqual(['business', 'CART_CHANGED']);
    expect(changed.message).toMatch(/Tu carrito cambió/);
    expect(classifyCheckoutError(http(409, { detail: { code: 'CART_LOCKED', message: 'x' } })).message).toMatch(/compra en curso/);
    expect(classifyPaymentError(http(409, { detail: { code: 'ORDER_NOT_PAYABLE', message: 'x', status: 'EXPIRED' } })).kind).toBe('business');
    expect(classifyPaymentError(http(401, { detail: 'x' })).kind).toBe('session');
  });

  it('reads the extra fields of a 409', () => {
    const stock = http(409, { detail: { code: 'INSUFFICIENT_STOCK', message: 'x', product_id: 4, order_id: 'abc' } });
    expect(extraNumbers(stock, 'product_id')).toEqual([4]);
    expect(extraString(stock, 'order_id')).toBe('abc');
    const missing = http(409, { detail: { code: 'PRODUCT_NOT_AVAILABLE', message: 'x', product_ids: [2, 'bad', 9] } });
    expect(extraNumbers(missing, 'product_ids')).toEqual([2, 9]);
    expect(extraString(new Error('x'), 'order_id')).toBeNull();
  });
});

describe('pending operations in sessionStorage', () => {
  beforeEach(() => sessionStorage.clear());
  afterEach(() => sessionStorage.clear());

  it('checkouts are kept per user and survive a reload of the module (they live in sessionStorage)', () => {
    saveCheckout({ userId: 7, cartVersion: 5, key: 'k-7', orderId: null });
    saveCheckout({ userId: 8, cartVersion: 2, key: 'k-8', orderId: null });
    expect(getCheckout(7)?.key).toBe('k-7');
    expect(getCheckout(8)?.key).toBe('k-8');
    expect(getCheckout(9)).toBeNull();
    clearCheckout(7);
    expect(getCheckout(7)).toBeNull();
    expect(getCheckout(8)?.key).toBe('k-8');
  });

  it('an old answer never removes a newer attempt', () => {
    saveCheckout({ userId: 7, cartVersion: 5, key: 'new-key', orderId: null });
    clearCheckout(7, 'old-key');
    expect(getCheckout(7)?.key).toBe('new-key');
    clearCheckout(7, 'new-key');
    expect(getCheckout(7)).toBeNull();
  });

  it('payments are kept per user and order', () => {
    savePayment({ userId: 7, orderId: 'o-1', key: 'p-1', simulation: 'SLOW_APPROVE' });
    savePayment({ userId: 7, orderId: 'o-2', key: 'p-2', simulation: 'APPROVE' });
    expect(getPayment(7, 'o-1')).toMatchObject({ key: 'p-1', simulation: 'SLOW_APPROVE' });
    expect(getPayment(8, 'o-1')).toBeNull();
    clearPayment(7, 'o-1');
    expect(getPayment(7, 'o-1')).toBeNull();
    expect(getPayment(7, 'o-2')?.key).toBe('p-2');
  });

  it('stores no token, password or profile; the key disappears when nothing is pending', () => {
    saveCheckout({ userId: 7, cartVersion: 5, key: newKey(), orderId: null });
    const raw = sessionStorage.getItem(PENDING_KEY)!;
    expect(Object.keys(JSON.parse(raw).checkouts[0]).sort()).toEqual(['cartVersion', 'key', 'orderId', 'savedAt', 'userId']);
    clearCheckout(7);
    expect(sessionStorage.getItem(PENDING_KEY)).toBeNull();
  });

  it('ignores corrupt or very old records', () => {
    sessionStorage.setItem(PENDING_KEY, '{not json');
    expect(getCheckout(7)).toBeNull();
    sessionStorage.setItem(PENDING_KEY, JSON.stringify({
      checkouts: [{ userId: 7, cartVersion: 5, key: 'k', orderId: null, savedAt: Date.now() - 25 * 3600_000 }, { userId: 'x' }],
      payments: [{ userId: 7, orderId: 'o', key: 'k', simulation: 'FREE_MONEY', savedAt: Date.now() }],
    }));
    expect(getCheckout(7)).toBeNull();
    expect(getPayment(7, 'o')).toBeNull();
  });

  it('new keys are random UUIDs', () => {
    const a = newKey();
    expect(a).toMatch(/^[0-9a-f-]{36}$/);
    expect(newKey()).not.toBe(a);
  });

  it('also outside a secure context, where randomUUID does not exist', () => {
    const original = crypto.randomUUID;
    Object.defineProperty(crypto, 'randomUUID', { value: undefined, configurable: true });
    try {
      const a = newKey();
      expect(a).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
      expect(newKey()).not.toBe(a);
    } finally {
      Object.defineProperty(crypto, 'randomUUID', { value: original, configurable: true });
    }
  });
});
