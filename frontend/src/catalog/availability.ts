// Availability shown for a product. Three different situations are never mixed up:
// sold out (0 units), no stock record (the product is missing from inventory-service)
// and inventory-service not answering (unknown). The figure is informative only:
// units are reserved for real at checkout.
import { useEffect, useState } from 'react';
import { getStock } from '../api/inventory';

export type StockLookup =
  | { status: 'loading' }
  | { status: 'ready'; byId: Map<number, number> }
  | { status: 'error' };

export type Availability =
  | { state: 'loading' }
  | { state: 'in-stock'; available: number }
  | { state: 'sold-out' }
  | { state: 'no-record' }
  | { state: 'unknown' };

export function availabilityOf(productId: number, lookup: StockLookup): Availability {
  if (lookup.status === 'loading') return { state: 'loading' };
  if (lookup.status === 'error') return { state: 'unknown' };
  const available = lookup.byId.get(productId);
  if (available === undefined) return { state: 'no-record' };
  return available > 0 ? { state: 'in-stock', available } : { state: 'sold-out' };
}

/** Sold out and unregistered products cannot be added; unknown stock can (checkout decides) */
export function canAddToCart(availability: Availability): boolean {
  return availability.state === 'in-stock' || availability.state === 'unknown';
}

/** Units known to be available, or null when the stock is unknown */
export function knownUnits(availability: Availability): number | null {
  return availability.state === 'in-stock' ? availability.available : null;
}

/** Reads the stock of the given products with one GET /inventory?ids= call */
export function useStock(productIds: number[]): StockLookup {
  const key = [...new Set(productIds)].sort((a, b) => a - b).join(',');
  const [lookup, setLookup] = useState<StockLookup>({ status: 'loading' });

  useEffect(() => {
    if (!key) {
      setLookup({ status: 'ready', byId: new Map() });
      return undefined;
    }
    const controller = new AbortController();
    setLookup({ status: 'loading' });
    getStock(key.split(',').map(Number), controller.signal)
      .then((rows) => {
        if (!controller.signal.aborted) setLookup({ status: 'ready', byId: new Map(rows.map((r) => [r.product_id, r.available])) });
      })
      .catch(() => {
        if (!controller.signal.aborted) setLookup({ status: 'error' });
      });
    return () => controller.abort();
  }, [key]);

  return lookup;
}
