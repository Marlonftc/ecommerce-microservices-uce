// Checkout: POST /orders with an Idempotency-Key saved BEFORE sending, so that the same
// attempt can be repeated after a 202 RESERVING, a timeout, a network error or a page
// reload. The key is never replaced while the outcome is unknown.
//   202 RESERVING        -> the same POST again after 2, 4 and 8 s, then a manual retry
//   uncertain            -> the same, with the same key and cart_version
//   definitive answer    -> the saved attempt is removed
import { useCallback, useEffect, useRef, useState } from 'react';
import { createOrder } from '../api/orders';
import { useAuth } from '../auth/AuthContext';
import { useCart } from '../cart/CartContext';
import { classifyCheckoutError, extraNumbers, extraString, type OrderProblem } from './orderErrors';
import { clearCheckout, getCheckout, newKey, saveCheckout, type PendingCheckout } from './pendingOps';

/** Waits before each automatic repetition of the same attempt */
export const RETRY_DELAYS_MS = [2000, 4000, 8000];

export type CheckoutPhase =
  | { name: 'idle' }
  | { name: 'sending'; recovering: boolean }
  | { name: 'waiting'; reason: 'reserving' | 'uncertain'; retry: number }
  | { name: 'stalled'; reason: 'reserving' | 'uncertain'; orderId: string | null }
  | { name: 'failed'; problem: OrderProblem; productIds: number[]; orderId: string | null }
  | { name: 'done'; orderId: string };

export function useCheckout() {
  const { user } = useAuth();
  const cart = useCart();
  const userId = user?.id ?? null;
  const reloadCart = cart.reload;

  const [phase, setPhase] = useState<CheckoutPhase>({ name: 'idle' });
  const sendingRef = useRef(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const runRef = useRef<{ alive: boolean; controller: AbortController }>({ alive: false, controller: new AbortController() });

  const stopTimer = () => {
    if (timerRef.current !== null) clearTimeout(timerRef.current);
    timerRef.current = null;
  };

  const send = useCallback(
    async (record: PendingCheckout, retry: number, recovering: boolean) => {
      const run = runRef.current;
      if (!run.alive || sendingRef.current) return; // never two POST of the same attempt at once
      sendingRef.current = true;
      stopTimer();
      setPhase({ name: 'sending', recovering });

      const scheduleAgain = (reason: 'reserving' | 'uncertain', current: PendingCheckout) => {
        if (retry < RETRY_DELAYS_MS.length) {
          setPhase({ name: 'waiting', reason, retry: retry + 1 });
          timerRef.current = setTimeout(() => void send(current, retry + 1, recovering), RETRY_DELAYS_MS[retry]);
        } else {
          setPhase({ name: 'stalled', reason, orderId: current.orderId });
        }
      };

      try {
        const { order } = await createOrder(record.cartVersion, record.key, run.controller.signal);
        if (!run.alive) return;
        if (order.status === 'RESERVING') {
          scheduleAgain('reserving', saveCheckout({ ...record, orderId: order.id }));
          return;
        }
        // Any other state (PENDING_PAYMENT, or CANCELLED by the sweep): the outcome is known
        clearCheckout(record.userId, record.key);
        void reloadCart();
        setPhase({ name: 'done', orderId: order.id });
      } catch (error) {
        if (!run.alive) return; // left the page or another user: the saved attempt stays for later
        const problem = classifyCheckoutError(error);
        if (problem.kind === 'uncertain') {
          scheduleAgain('uncertain', record);
          return;
        }
        clearCheckout(record.userId, record.key);
        if (problem.kind === 'business') void reloadCart();
        setPhase({
          name: 'failed',
          problem,
          productIds: [...extraNumbers(error, 'product_ids'), ...extraNumbers(error, 'product_id')],
          orderId: extraString(error, 'order_id'),
        });
      } finally {
        sendingRef.current = false;
      }
    },
    [reloadCart],
  );

  // A new page or another user: recover the attempt saved for THIS user, if any
  useEffect(() => {
    const run = { alive: true, controller: new AbortController() };
    runRef.current = run;
    setPhase({ name: 'idle' });
    if (userId !== null) {
      const saved = getCheckout(userId);
      if (saved) void send(saved, 0, true);
    }
    return () => {
      run.alive = false;
      run.controller.abort();
      stopTimer();
      sendingRef.current = false;
    };
  }, [userId, send]);

  /** A NEW attempt for the cart version the user just reviewed */
  const confirm = useCallback(() => {
    if (userId === null || !cart.cart || sendingRef.current || timerRef.current !== null) return;
    const record = saveCheckout({ userId, cartVersion: cart.cart.version, key: newKey(), orderId: null });
    void send(record, 0, false);
  }, [userId, cart.cart, send]);

  /** Repeats the saved attempt now, with its original key */
  const retryNow = useCallback(() => {
    if (userId === null) return;
    const saved = getCheckout(userId);
    if (saved) void send(saved, 0, true);
    else setPhase({ name: 'idle' });
  }, [userId, send]);

  const reset = useCallback(() => setPhase({ name: 'idle' }), []);

  return { phase, confirm, retryNow, reset };
}
