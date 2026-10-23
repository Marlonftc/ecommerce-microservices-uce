// Unread counter of the signed-in customer (the bell in the header). An admin has no bell:
// notification-service returns every customer's notifications to an admin, so a counter
// would mix them (the read-only admin view comes in a later stage).
//
// The count is the `total` of GET /notifications?unread_only=true&size=1. It is read when
// the session starts, when the customer opens the orders or notifications pages, after an
// order reaches a final state, right after marking a notification as read, every 60 s while
// the tab is visible and when the tab becomes visible again.
// - Only one request at a time: a refresh asked while one is running joins it; a forced
//   refresh (after a change) runs once more when the running one ends.
// - Each session has its own epoch and AbortController: logging out or switching users
//   cancels timers and requests, and late answers of the previous session are ignored.
// - A failed read leaves the count unknown (null), never 0.
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { useLocation } from 'react-router-dom';
import { countUnread } from '../api/notifications';
import { useAuth } from '../auth/AuthContext';

export const UNREAD_POLL_MS = 60_000;

export type UnreadStatus = 'off' | 'loading' | 'ready' | 'error';

export interface NotificationsValue {
  /** 'off' when there is no customer session */
  status: UnreadStatus;
  /** Unread notifications, or null when unknown (loading or error) */
  unread: number | null;
  /** force: the data changed, so a read already running is not enough */
  refresh(options?: { force?: boolean }): void;
}

interface Session {
  userId: number | null;
  controller: AbortController;
  running: boolean;
  again: boolean;
}

const NotificationsContext = createContext<NotificationsValue | null>(null);

const newSession = (userId: number | null): Session => ({ userId, controller: new AbortController(), running: false, again: false });

/** Pages whose visit refreshes the counter */
const REFRESH_PATHS = /^\/(notificaciones|pedidos)(\/|$)/;

export function NotificationsProvider({ children }: { children: ReactNode }) {
  const { status: authStatus, user } = useAuth();
  const customerId = authStatus === 'authenticated' && user?.role === 'customer' ? user.id : null;
  const sessionRef = useRef<Session>(newSession(null));
  const [state, setState] = useState<{ status: UnreadStatus; unread: number | null }>({ status: 'off', unread: null });

  const read = useCallback((session: Session) => {
    if (session.userId === null || sessionRef.current !== session) return;
    session.running = true;
    session.again = false;
    setState((s) => (s.status === 'ready' ? s : { status: 'loading', unread: null }));
    countUnread(session.controller.signal)
      .then((total) => {
        if (sessionRef.current === session) setState({ status: 'ready', unread: total });
      })
      .catch(() => {
        if (sessionRef.current === session && !session.controller.signal.aborted) setState({ status: 'error', unread: null });
      })
      .finally(() => {
        session.running = false;
        if (session.again) read(session);
      });
  }, []);

  const refresh = useCallback(
    (options?: { force?: boolean }) => {
      const session = sessionRef.current;
      if (session.userId === null) return;
      if (session.running) {
        if (options?.force) session.again = true;
        return;
      }
      read(session);
    },
    [read],
  );

  // New customer session (or none): cancel everything of the previous one
  useEffect(() => {
    sessionRef.current.controller.abort();
    const session = newSession(customerId);
    sessionRef.current = session;
    if (customerId === null) {
      setState({ status: 'off', unread: null });
      return undefined;
    }
    setState({ status: 'loading', unread: null });
    read(session);

    const timer = setInterval(() => {
      if (document.visibilityState === 'visible') refresh();
    }, UNREAD_POLL_MS);
    const onVisibility = () => {
      if (document.visibilityState === 'visible') refresh();
    };
    document.addEventListener('visibilitychange', onVisibility);
    return () => {
      clearInterval(timer);
      document.removeEventListener('visibilitychange', onVisibility);
      session.controller.abort();
    };
  }, [customerId, read, refresh]);

  // Visiting the orders or notifications pages
  const { pathname } = useLocation();
  useEffect(() => {
    if (REFRESH_PATHS.test(pathname)) refresh();
  }, [pathname, refresh]);

  const value = useMemo<NotificationsValue>(() => ({ ...state, refresh }), [state, refresh]);
  return <NotificationsContext.Provider value={value}>{children}</NotificationsContext.Provider>;
}

export function useNotifications(): NotificationsValue {
  const value = useContext(NotificationsContext);
  if (!value) throw new Error('useNotifications must be used inside <NotificationsProvider>');
  return value;
}
