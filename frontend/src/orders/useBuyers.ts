// Names and emails of the customers behind a set of orders, for an admin. order-service
// keeps only user_id, so the data comes from user-service with ONE grouped request
// (GET /api/users?ids=) per set of ids. Failure never blocks the orders: the page then
// shows "Cliente #ID".
import { useEffect, useState } from 'react';
import { getUsersByIds, uniqueUserIds } from '../api/users';
import type { UserOut } from '../api/types';

export type BuyersStatus = 'idle' | 'loading' | 'ready' | 'error';

export interface Buyers {
  status: BuyersStatus;
  /** Only users that exist; a missing id falls back to "Cliente #ID" */
  byId: ReadonlyMap<number, UserOut>;
}

interface State extends Buyers {
  key: string;
}

const EMPTY: ReadonlyMap<number, UserOut> = new Map();

/**
 * `enabled` must be true only for an admin. The request key is the sorted list of unique
 * ids, so the same page never asks twice and an answer for another page is never used.
 */
export function useBuyers(userIds: readonly number[], enabled: boolean): Buyers {
  const key = enabled ? uniqueUserIds(userIds).join(',') : '';
  const [state, setState] = useState<State>({ key: '', status: 'idle', byId: EMPTY });

  useEffect(() => {
    if (!key) return undefined;
    const controller = new AbortController();
    setState({ key, status: 'loading', byId: EMPTY });
    getUsersByIds(key.split(',').map(Number), controller.signal)
      .then((users) => {
        if (!controller.signal.aborted) setState({ key, status: 'ready', byId: new Map(users.map((u) => [u.id, u])) });
      })
      .catch(() => {
        if (!controller.signal.aborted) setState({ key, status: 'error', byId: EMPTY });
      });
    return () => controller.abort();
  }, [key]);

  if (!key) return { status: 'idle', byId: EMPTY };
  // Until the answer for THIS key arrives, nothing from another page is shown
  return state.key === key ? state : { status: 'loading', byId: EMPTY };
}

/** "Ana Pérez" or the fallback "Cliente #7" */
export function buyerName(userId: number, buyers: Buyers): string {
  return buyers.byId.get(userId)?.full_name ?? `Cliente #${userId}`;
}
