// Reads one order and, while it still changes by itself (payment being resolved by the
// worker, inventory being confirmed or released, expiry), reads it again every 3 s for at
// most 2 minutes. After that the user can refresh by hand. Timers and requests stop when
// the page is left or the user changes. A reading sent before a newer answer (for
// example the answer of POST /pay) is ignored, so an old state never comes back.
import { useCallback, useEffect, useRef, useState } from 'react';
import { getOrder } from '../api/orders';
import type { OrderOut } from '../api/types';
import { useAuth } from '../auth/AuthContext';
import { isSettling } from './orderStatus';

export const POLL_INTERVAL_MS = 3000;
export const POLL_WINDOW_MS = 120_000;

export interface OrderTracker {
  order: OrderOut | null;
  status: 'loading' | 'ready' | 'error';
  error: unknown;
  /** Automatic reading is running */
  polling: boolean;
  /** The 2-minute window ended while the order was still changing */
  pollingStopped: boolean;
  /** Reads the order now and restarts the automatic reading if it is still changing */
  refresh(): Promise<OrderOut | null>;
  /** Shows an order received from another call (pay, cancel) */
  update(order: OrderOut): void;
}

type TrackerState = Omit<OrderTracker, 'refresh' | 'update'>;

const INITIAL: TrackerState = { order: null, status: 'loading', error: null, polling: false, pollingStopped: false };

export function useOrderTracker(orderId: string): OrderTracker {
  const { user } = useAuth();
  const userId = user?.id ?? null;
  const [state, setState] = useState<TrackerState>(INITIAL);

  const runRef = useRef({ alive: false, controller: new AbortController() });
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const windowEndsRef = useRef(0);
  const seqRef = useRef(0); // grows with every reading sent and every order shown from elsewhere
  const readingRef = useRef<{ seq: number; promise: Promise<OrderOut | null> } | null>(null);
  const readRef = useRef<(restartWindow: boolean) => Promise<OrderOut | null>>(() => Promise.resolve(null));

  const stopTimer = () => {
    if (timerRef.current !== null) clearTimeout(timerRef.current);
    timerRef.current = null;
  };

  const show = useCallback((order: OrderOut, restartWindow: boolean) => {
    if (!runRef.current.alive) return;
    stopTimer();
    if (!isSettling(order)) {
      setState({ order, status: 'ready', error: null, polling: false, pollingStopped: false });
      return;
    }
    if (restartWindow) windowEndsRef.current = Date.now() + POLL_WINDOW_MS;
    if (Date.now() >= windowEndsRef.current) {
      setState({ order, status: 'ready', error: null, polling: false, pollingStopped: true });
      return;
    }
    setState({ order, status: 'ready', error: null, polling: true, pollingStopped: false });
    timerRef.current = setTimeout(() => void readRef.current(false), POLL_INTERVAL_MS);
  }, []);

  const read = useCallback(
    (restartWindow: boolean): Promise<OrderOut | null> => {
      const run = runRef.current;
      if (!run.alive) return Promise.resolve(null);
      if (readingRef.current) return readingRef.current.promise; // one GET at a time
      const seq = ++seqRef.current;
      const promise = getOrder(orderId, run.controller.signal)
        .then((order) => {
          if (!run.alive || seq !== seqRef.current) return null; // a newer state is already shown
          show(order, restartWindow);
          return order;
        })
        .catch((error: unknown) => {
          if (!run.alive || seq !== seqRef.current) return null;
          stopTimer();
          setState((s) => ({ ...s, status: s.order ? 'ready' : 'error', error, polling: false, pollingStopped: s.order !== null }));
          return null;
        })
        .finally(() => {
          if (readingRef.current?.promise === promise) readingRef.current = null;
        });
      readingRef.current = { seq, promise };
      return promise;
    },
    [orderId, show],
  );
  readRef.current = read;

  useEffect(() => {
    const run = { alive: true, controller: new AbortController() };
    runRef.current = run;
    readingRef.current = null;
    setState(INITIAL);
    if (userId !== null) void read(true);
    return () => {
      run.alive = false;
      run.controller.abort();
      stopTimer();
    };
  }, [orderId, userId, read]);

  const refresh = useCallback(() => read(true), [read]);

  const update = useCallback(
    (order: OrderOut) => {
      seqRef.current += 1; // any reading still in flight is older than this answer
      readingRef.current = null;
      show(order, true);
    },
    [show],
  );

  return { ...state, refresh, update };
}
