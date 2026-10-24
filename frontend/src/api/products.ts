// product-service through the gateway: /api/products. The public catalogue never asks
// for inactive products; include_inactive and the writing calls are admin only (and
// product-service checks the role again).
import { http } from './http';
import type { OffsetPage, ProductIn, ProductOut, ProductUpdate } from './types';

/** Largest page product-service accepts (limit <= 100) */
export const MAX_PAGE_SIZE = 100;
/** Categories are read from at most this many pages of 100 active products */
export const CATEGORY_SCAN_MAX_REQUESTS = 5;

export interface ProductQuery {
  q?: string;
  category?: string;
  /** Admin only: also inactive products */
  includeInactive?: boolean;
  limit: number;
  offset: number;
}

/** GET /api/products: active products (and inactive ones for an admin), ordered by id */
export async function listProducts(query: ProductQuery, signal?: AbortSignal): Promise<OffsetPage<ProductOut>> {
  const { q, category, includeInactive, limit, offset } = query;
  const include_inactive = includeInactive ? true : undefined;
  return (await http.get<OffsetPage<ProductOut>>('/products', { query: { q, category, include_inactive, limit, offset }, signal })).data;
}

/** POST /api/products (admin): 201 created, 409 SKU already exists, 422 invalid data.
 *  There is no Idempotency-Key: an unknown outcome is checked, never repeated blindly. */
export async function createProduct(data: ProductIn, signal?: AbortSignal): Promise<ProductOut> {
  return (await http.post<ProductOut>('/products', data, { signal })).data;
}

/** PUT /api/products/{id} (admin): full replacement, is_active included; repeating it is harmless */
export async function replaceProduct(id: number, data: ProductUpdate, signal?: AbortSignal): Promise<ProductOut> {
  return (await http.put<ProductOut>(`/products/${id}`, data, { signal })).data;
}

/** DELETE /api/products/{id} (admin): 204, the product is only deactivated */
export async function deactivateProduct(id: number, signal?: AbortSignal): Promise<void> {
  await http.delete<void>(`/products/${id}`, { signal });
}

/** GET /api/products/{id}: 404 when it does not exist or is inactive (an admin also sees inactive ones) */
export async function getProduct(id: number, signal?: AbortSignal): Promise<ProductOut> {
  return (await http.get<ProductOut>(`/products/${id}`, { signal })).data;
}

export interface CategoryScan {
  /** Distinct categories, sorted */
  categories: string[];
  /** false when the catalogue has more active products than were read */
  complete: boolean;
  scanned: number;
  total: number;
}

/**
 * product-service has no categories endpoint, so they are collected from the active
 * products themselves, 100 per request and at most CATEGORY_SCAN_MAX_REQUESTS requests.
 * With more than 500 active products the list can be incomplete; `complete` says so.
 */
export async function listCategories(signal?: AbortSignal): Promise<CategoryScan> {
  const found = new Set<string>();
  let scanned = 0;
  let total = 0;
  for (let request = 0; request < CATEGORY_SCAN_MAX_REQUESTS; request += 1) {
    const page = await listProducts({ limit: MAX_PAGE_SIZE, offset: request * MAX_PAGE_SIZE }, signal);
    total = page.total;
    scanned += page.items.length;
    page.items.forEach((product) => found.add(product.category));
    if (page.items.length < MAX_PAGE_SIZE || scanned >= total) break;
  }
  return {
    categories: [...found].sort((a, b) => a.localeCompare(b, 'es')),
    complete: scanned >= total,
    scanned,
    total,
  };
}
