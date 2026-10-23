// Finds the active order (RESERVING, PENDING_PAYMENT or PAYMENT_PENDING) of the signed-in
// user, to link to it when the cart is locked. For an admin GET /orders returns every
// customer's orders, so only orders whose user_id is the viewer's own are considered.
import { useEffect, useState } from 'react';
import { listOrders } from '../api/orders';
import type { OrderOut } from '../api/types';
import { useAuth } from '../auth/AuthContext';
import { isActive } from './orderStatus';

export type ActiveOrderLookup = { status: 'idle' | 'loading' | 'none' | 'error' } | { status: 'found'; order: OrderOut };

/** Orders are listed newest first; an active order is always among the most recent */
const LOOKUP_SIZE = 20;

export function findOwnActiveOrder(orders: OrderOut[], userId: number): OrderOut | null {
  return orders.find((order) => order.user_id === userId && isActive(order)) ?? null;
}

export function useActiveOrder(enabled: boolean): ActiveOrderLookup {
  const { user } = useAuth();
  const userId = user?.id ?? null;
  const [lookup, setLookup] = useState<ActiveOrderLookup>({ status: 'idle' });

  useEffect(() => {
    if (!enabled || userId === null) {
      setLookup({ status: 'idle' });
      return undefined;
    }
    const controller = new AbortController();
    setLookup({ status: 'loading' });
    listOrders(1, LOOKUP_SIZE, controller.signal)
      .then((page) => {
        if (controller.signal.aborted) return;
        const order = findOwnActiveOrder(page.items, userId);
        setLookup(order ? { status: 'found', order } : { status: 'none' });
      })
      .catch(() => {
        if (!controller.signal.aborted) setLookup({ status: 'error' });
      });
    return () => controller.abort();
  }, [enabled, userId]);

  return lookup;
}
