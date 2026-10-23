// inventory-service through the gateway: /api/inventory (public stock view).
import { http } from './http';
import type { StockPublic } from './types';

export const MAX_STOCK_IDS = 100;

/**
 * GET /api/inventory?ids=1,2,3. A product without a stock record is simply missing
 * from the answer; the caller must not read that as "0 units".
 */
export async function getStock(productIds: number[], signal?: AbortSignal): Promise<StockPublic[]> {
  const ids = [...new Set(productIds)];
  if (ids.length === 0 || ids.length > MAX_STOCK_IDS) {
    throw new Error(`getStock needs between 1 and ${MAX_STOCK_IDS} product ids`);
  }
  return (await http.get<StockPublic[]>('/inventory', { query: { ids: ids.join(',') }, signal })).data;
}
