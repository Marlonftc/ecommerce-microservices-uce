// inventory-service through the gateway: /api/inventory. Anyone reads the stock; with an
// admin token the same calls also return reserved units. Adjustments are admin only.
import { http } from './http';
import type { AdjustmentIn, AdjustmentOut, StockAdmin, StockPublic } from './types';

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

/** GET /api/inventory/{id} as an admin. 404 = the product has no stock record (not "0 units") */
export async function getStockRecord(productId: number, signal?: AbortSignal): Promise<StockAdmin> {
  return (await http.get<StockAdmin>(`/inventory/${productId}`, { signal })).data;
}

/** POST /api/inventory/{id}/adjustments: relative change (delta), 201 applied, 200 repeated key */
export async function adjustStock(
  productId: number,
  body: AdjustmentIn,
  key: string,
  signal?: AbortSignal,
): Promise<{ adjustment: AdjustmentOut; status: number }> {
  if (key.length < 1 || key.length > 100) throw new Error('Idempotency-Key must have 1 to 100 characters');
  const response = await http.post<AdjustmentOut>(`/inventory/${productId}/adjustments`, body, {
    headers: { 'Idempotency-Key': key },
    signal,
  });
  return { adjustment: response.data, status: response.status };
}

/** GET /api/inventory/{id}/adjustments?limit&offset: newest first, plain list (no total) */
export async function listAdjustments(productId: number, limit: number, offset: number, signal?: AbortSignal): Promise<AdjustmentOut[]> {
  return (await http.get<AdjustmentOut[]>(`/inventory/${productId}/adjustments`, { query: { limit, offset }, signal })).data;
}
