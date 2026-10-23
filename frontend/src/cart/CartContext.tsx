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
//   other change is allowed until a GET /cart sent AFTER that failure answers.
// - Versions only grow, so an answer with an older version never replaces a newer cart.
// - When the user signs out or another user signs in, pending requests are aborted and
//   any late answer of the old session is ignored.
//
// Shared reads: while a GET /cart is pending, other reads of the same session and the
// same "generation" wait for it instead of sending another request. The generation
// changes when a change (PUT/DELETE) is sent and when it ends, so a read started before
// a change is never reused afterwards; this does not depend on cart.version, because
// `locked` can change without a new version. This only removes duplicate reads inside
// ONE tab: tabs do not share memory and still read the cart on their own.
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
  /** At least one GET /cart is pending */
  refreshing: boolean;
  busy: boolean;
  /** The outcome of the last change is unknown until the cart is read again */
  needsReload: boolean;
}

export interface ReloadOptions {
  /** Stops waiting for this caller only; a read shared with others keeps running */
  signal?: AbortSignal;
}

export interface CartContextValue extends Omit<CartState, 'owner'> {
  /** Sum of the quantities (header badge) */
  itemCount: number;
  quantityOf(productId: number): number;
  /** Reads the cart; resolves with the shown cart, or null if the read failed or was left */
  reload(options?: ReloadOptions): Promise<CartOut | null>;
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

/** A pending GET /cart that later reads of the same session and generation can join */
interface SharedRead {
  epoch: number;
  generation: number;
  promise: Promise<CartOut | null>;
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
  const uncertainSeqRef = useRef<number | null>(null); // last request sent before an uncertain failure
  const generationRef = useRef(0); // changes when a cart change starts and when it ends
  const sharedReadRef = useRef<SharedRead | null>(null);
  const pendingReadsRef = useRef(0);

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

  /** Sends a GET /cart, or joins the pending one of the same session and generation */
  const startRead = useCallback((): Promise<CartOut | null> => {
    const { epoch, userId: owner, controller } = sessionRef.current;
    if (owner === null) return Promise.resolve(null);
    const shared = sharedReadRef.current;
    if (shared && shared.epoch === epoch && shared.generation === generationRef.current) return shared.promise;

    const seq = ++seqRef.current;
    pendingReadsRef.current += 1;
    setState((s) => ({ ...s, refreshing: true, status: s.cart ? s.status : 'loading' }));

    const run = async (): Promise<CartOut | null> => {
      try {
        const cart = await cartApi.getCart(controller.signal);
        if (!isCurrent(epoch)) return null;
        accept(cart, seq, epoch);
        // Only a read SENT after an uncertain failure proves what happened to that change
        const settled = uncertainSeqRef.current === null || seq > uncertainSeqRef.current;
        if (settled) uncertainSeqRef.current = null;
        setState((s) => ({ ...s, loadError: null, needsReload: !settled }));
        return cartRef.current;
      } catch (error) {
        if (!isCurrent(epoch) || isAbort(error)) return null;
        setState((s) => ({ ...s, loadError: error, status: s.cart ? 'ready' : 'error' }));
        return null;
      } finally {
        if (isCurrent(epoch)) {
          pendingReadsRef.current -= 1;
          const stillReading = pendingReadsRef.current > 0;
          setState((s) => ({ ...s, refreshing: stillReading }));
        }
      }
    };

    const entry: SharedRead = { epoch, generation: generationRef.current, promise: Promise.resolve(null) };
    entry.promise = run().finally(() => {
      // Released on success and on failure; an older read never removes a newer one
      if (sharedReadRef.current === entry) sharedReadRef.current = null;
    });
    sharedReadRef.current = entry;
    return entry.promise;
  }, [accept]);

  const reload = useCallback(
    (options?: ReloadOptions): Promise<CartOut | null> => {
      const signal = options?.signal;
      if (signal?.aborted) return Promise.resolve(null);
      const read = startRead();
      if (!signal) return read;
      // This caller stops waiting; the request itself (maybe shared) is not cancelled
      return new Promise<CartOut | null>((resolve) => {
        const leave = () => resolve(null);
        signal.addEventListener('abort', leave, { once: true });
        void read.then(resolve).finally(() => signal.removeEventListener('abort', leave));
      });
    },
    [startRead],
  );

  // A new session (sign in, sign out, another user): forget everything of the old one
  useEffect(() => {
    const controller = new AbortController();
    sessionRef.current = { epoch: sessionRef.current.epoch + 1, userId, controller };
    cartRef.current = null;
    appliedSeqRef.current = 0;
    busyRef.current = false;
    uncertainSeqRef.current = null;
    sharedReadRef.current = null; // never reused by the next session
    pendingReadsRef.current = 0;
    setState(userId === null ? EMPTY : { ...EMPTY, owner: userId, status: 'loading' });
    if (userId !== null) void startRead();
    return () => controller.abort();
  }, [userId, startRead]);

  const mutate = useCallback(
    async (plan: (cart: CartOut) => CartProblem | Step): Promise<CartResult> => {
      const { epoch, userId: owner, controller } = sessionRef.current;
      if (owner === null) return { ok: false, problem: problem('session'), cart: null };
      if (busyRef.current) return { ok: false, problem: problem('busy'), cart: null };
      if (uncertainSeqRef.current !== null) return { ok: false, problem: problem('needs-reload'), cart: cartRef.current };

      busyRef.current = true;
      setState((s) => ({ ...s, busy: true }));
      try {
        const cart = cartRef.current ?? (await startRead());
        if (!isCurrent(epoch)) return { ok: false, problem: problem('cancelled'), cart: null };
        if (!cart) return { ok: false, problem: problem('load-failed'), cart: null };
        if (cart.locked) return { ok: false, problem: problem('locked'), cart };

        const step = plan(cart);
        if (typeof step !== 'function') return { ok: false, problem: step, cart };

        // The quantity and expected_version computed above are sent once, as they are.
        // Reads started before this point are not shared any more.
        generationRef.current += 1;
        const seq = ++seqRef.current;
        let outcome: { updated: CartOut } | { error: unknown };
        try {
          outcome = { updated: await step(controller.signal) };
        } catch (error) {
          outcome = { error };
        }
        // ...and neither are reads started while the change was running
        generationRef.current += 1;
        if (!isCurrent(epoch)) return { ok: false, problem: problem('cancelled'), cart: null };

        if ('updated' in outcome) {
          accept(outcome.updated, seq, epoch);
          return { ok: true, cart: outcome.updated };
        }
        if (isAbort(outcome.error)) return { ok: false, problem: problem('cancelled'), cart: null };
        const found = classifyCartError(outcome.error);
        if (found.kind === 'uncertain') {
          // Only reads sent from now on can tell whether the change was applied
          uncertainSeqRef.current = seqRef.current;
          setState((s) => ({ ...s, needsReload: true }));
        }
        let reloaded: CartOut | null = null;
        if (found.kind === 'uncertain' || found.kind === 'changed' || found.kind === 'locked') {
          reloaded = await startRead(); // always a new GET: the generation changed above
        }
        return { ok: false, problem: found, cart: reloaded };
      } finally {
        if (isCurrent(epoch)) {
          busyRef.current = false;
          setState((s) => ({ ...s, busy: false }));
        }
      }
    },
    [accept, startRead],
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
    const visible = state.owner === userId ? state : ({ ...EMPTY, status: userId === null ? 'idle' : 'loading' } as CartState);
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
