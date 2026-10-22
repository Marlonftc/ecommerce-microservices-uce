// Cart of the signed-in user. The cart lives only in order-service (PostgreSQL): this
// provider keeps in memory the last cart CONFIRMED by the backend and never stores it
// in the browser.
//
// Rules that keep it consistent:
// - Changes run one at a time (an internal lock, not only disabled buttons). A change
//   asked for while another runs is not sent.
// - Every change sends expected_version = the version of the last confirmed cart.
//   CART_CHANGED / CART_LOCKED: the cart is read again and the change is NOT repeated.
// - Timeout, network error or gateway 502/504: the change may have been applied. No
//   other change is allowed until GET /cart answers again.
// - Versions only grow, so an answer with an older version never replaces a newer cart.
// - When the user signs out or another user signs in, pending requests are aborted and
//   any late answer of the old session is ignored.
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import * as cartApi from '../api/cart';
import type { CartOut } from '../api/types';
import { useAuth } from '../auth/AuthContext';
import { classifyCartError, problem, type CartProblem } from './cartErrors';
import { MAX_QUANTITY, MIN_QUANTITY } from './quantity';

export type CartStatus = 'idle' | 'loading' | 'ready' | 'error';

/** `cart` on a failure is the cart read again afterwards, when that read succeeded */
export type CartResult = { ok: true; cart: CartOut } | { ok: false; problem: CartProblem; cart: CartOut | null };

interface CartState {
  /** User the state belongs to; anything else is never shown */
  owner: number | null;
  cart: CartOut | null;
  status: CartStatus;
  /** Last failed read; a cart read earlier may still be shown */
  loadError: unknown;
  refreshing: boolean;
  busy: boolean;
  /** The outcome of the last change is unknown until the cart is read again */
  needsReload: boolean;
}

export interface CartContextValue extends Omit<CartState, 'owner'> {
  /** Sum of the quantities (header badge) */
  itemCount: number;
  quantityOf(productId: number): number;
  reload(): Promise<CartOut | null>;
  /** Adds units to what the confirmed cart already holds (PUT sends the absolute total) */
  addItem(productId: number, units: number): Promise<CartResult>;
  setQuantity(productId: number, quantity: number): Promise<CartResult>;
  removeItem(productId: number): Promise<CartResult>;
  clear(): Promise<CartResult>;
}

const EMPTY: CartState = {
  owner: null, cart: null, status: 'idle', loadError: null, refreshing: false, busy: false, needsReload: false,
};

interface CartSession {
  epoch: number;
  userId: number | null;
  controller: AbortController;
}

type Step = (signal: AbortSignal) => Promise<CartOut>;

const CartContext = createContext<CartContextValue | null>(null);

function isAbort(error: unknown): boolean {
  return typeof error === 'object' && error !== null && (error as { name?: unknown }).name === 'AbortError';
}

function validQuantity(value: number): boolean {
  return Number.isInteger(value) && value >= MIN_QUANTITY && value <= MAX_QUANTITY;
}

export function CartProvider({ children }: { children: ReactNode }) {
  const { status: authStatus, user } = useAuth();
  const userId = authStatus === 'authenticated' && user ? user.id : null;

  const [state, setState] = useState<CartState>(EMPTY);
  const sessionRef = useRef<CartSession>({ epoch: 0, userId: null, controller: new AbortController() });
  const cartRef = useRef<CartOut | null>(null);
  const seqRef = useRef(0); // order in which requests were sent
  const appliedSeqRef = useRef(0); // request whose cart is shown
  const busyRef = useRef(false);
  const uncertainSeqRef = useRef<number | null>(null); // change with unknown outcome

  const isCurrent = (epoch: number) => sessionRef.current.epoch === epoch;

  /** Shows a cart from the backend unless a newer one is already shown */
  const accept = useCallback((incoming: CartOut, seq: number, epoch: number) => {
    if (sessionRef.current.epoch !== epoch) return;
    const current = cartRef.current;
    const newer =
      current === null ||
      incoming.version > current.version ||
      (incoming.version === current.version && seq > appliedSeqRef.current);
    if (!newer) return;
    cartRef.current = incoming;
    appliedSeqRef.current = seq;
    setState((s) => ({ ...s, cart: incoming, status: 'ready' }));
  }, []);

  const reload = useCallback(async (): Promise<CartOut | null> => {
    const { epoch, userId: owner, controller } = sessionRef.current;
    if (owner === null) return null;
    const seq = ++seqRef.current;
    setState((s) => ({ ...s, refreshing: true, status: s.cart ? s.status : 'loading' }));
    try {
      const cart = await cartApi.getCart(controller.signal);
      if (sessionRef.current.epoch !== epoch) return null;
      accept(cart, seq, epoch);
      // Only a read SENT after the uncertain change proves what happened to it
      const settled = uncertainSeqRef.current === null || seq > uncertainSeqRef.current;
      if (settled) uncertainSeqRef.current = null;
      setState((s) => ({ ...s, refreshing: false, loadError: null, needsReload: !settled }));
      return cartRef.current;
    } catch (error) {
      if (sessionRef.current.epoch !== epoch || isAbort(error)) return null;
      setState((s) => ({ ...s, refreshing: false, loadError: error, status: s.cart ? 'ready' : 'error' }));
      return null;
    }
  }, [accept]);

  // A new session (sign in, sign out, another user): forget everything of the old one
  useEffect(() => {
    const controller = new AbortController();
    sessionRef.current = { epoch: sessionRef.current.epoch + 1, userId, controller };
    cartRef.current = null;
    appliedSeqRef.current = 0;
    busyRef.current = false;
    uncertainSeqRef.current = null;
    setState(userId === null ? EMPTY : { ...EMPTY, owner: userId, status: 'loading' });
    if (userId !== null) void reload();
    return () => controller.abort();
  }, [userId, reload]);

  const mutate = useCallback(
    async (plan: (cart: CartOut) => CartProblem | Step): Promise<CartResult> => {
      const { epoch, userId: owner, controller } = sessionRef.current;
      if (owner === null) return { ok: false, problem: problem('session'), cart: null };
      if (busyRef.current) return { ok: false, problem: problem('busy'), cart: null };
      if (uncertainSeqRef.current !== null) return { ok: false, problem: problem('needs-reload'), cart: cartRef.current };

      busyRef.current = true;
      setState((s) => ({ ...s, busy: true }));
      try {
        const cart = cartRef.current ?? (await reload());
        if (!isCurrent(epoch)) return { ok: false, problem: problem('cancelled'), cart: null };
        if (!cart) return { ok: false, problem: problem('load-failed'), cart: null };
        if (cart.locked) return { ok: false, problem: problem('locked'), cart };

        const step = plan(cart);
        if (typeof step !== 'function') return { ok: false, problem: step, cart };

        // The quantity and expected_version computed above are sent once, as they are
        const seq = ++seqRef.current;
        try {
          const updated = await step(controller.signal);
          if (!isCurrent(epoch)) return { ok: false, problem: problem('cancelled'), cart: null };
          accept(updated, seq, epoch);
          return { ok: true, cart: updated };
        } catch (error) {
          if (!isCurrent(epoch) || isAbort(error)) return { ok: false, problem: problem('cancelled'), cart: null };
          const found = classifyCartError(error);
          if (found.kind === 'uncertain') {
            uncertainSeqRef.current = seq;
            setState((s) => ({ ...s, needsReload: true }));
          }
          let reloaded: CartOut | null = null;
          if (found.kind === 'uncertain' || found.kind === 'changed' || found.kind === 'locked') {
            reloaded = await reload();
          }
          return { ok: false, problem: found, cart: reloaded };
        }
      } finally {
        if (isCurrent(epoch)) {
          busyRef.current = false;
          setState((s) => ({ ...s, busy: false }));
        }
      }
    },
    [accept, reload],
  );

  const addItem = useCallback(
    (productId: number, units: number) =>
      mutate((cart) => {
        const current = cart.items.find((item) => item.product_id === productId)?.quantity ?? 0;
        const quantity = current + units;
        if (!Number.isInteger(units) || units < 1 || !validQuantity(quantity)) return problem('invalid');
        return (signal) => cartApi.setItem(productId, quantity, cart.version, signal);
      }),
    [mutate],
  );

  const setQuantity = useCallback(
    (productId: number, quantity: number) =>
      mutate((cart) => {
        if (!validQuantity(quantity)) return problem('invalid');
        return (signal) => cartApi.setItem(productId, quantity, cart.version, signal);
      }),
    [mutate],
  );

  const removeItem = useCallback(
    (productId: number) => mutate((cart) => (signal) => cartApi.removeItem(productId, cart.version, signal)),
    [mutate],
  );

  const clear = useCallback(() => mutate((cart) => (signal) => cartApi.clearCart(cart.version, signal)), [mutate]);

  const value = useMemo<CartContextValue>(() => {
    // State of another user (one render before the session effect runs) is never exposed
    const visible = state.owner === userId ? state : { ...EMPTY, status: userId === null ? 'idle' : 'loading' } as CartState;
    const { owner: _owner, ...rest } = visible;
    const items = visible.cart?.items ?? [];
    return {
      ...rest,
      itemCount: items.reduce((sum, item) => sum + item.quantity, 0),
      quantityOf: (productId: number) => items.find((item) => item.product_id === productId)?.quantity ?? 0,
      reload,
      addItem,
      setQuantity,
      removeItem,
      clear,
    };
  }, [state, userId, reload, addItem, setQuantity, removeItem, clear]);

  return <CartContext.Provider value={value}>{children}</CartContext.Provider>;
}

export function useCart(): CartContextValue {
  const value = useContext(CartContext);
  if (!value) throw new Error('useCart must be used inside <CartProvider>');
  return value;
}
