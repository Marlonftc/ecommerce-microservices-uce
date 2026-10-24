// After a POST /products with an unknown outcome (timeout, network, gateway error), finds
// out whether the product was created. product-service has no SKU filter, only a name
// search (ILIKE), so the products whose name contains the sent name are scanned for the
// exact SKU. The answer is only as strong as the evidence:
//   same       a product with that SKU holds exactly the sent data
//   different  a product with that SKU exists with other data: it cannot be called ours
//   absent     EVERY match of the search was read and none has that SKU
//   unknown    the search failed or had more matches than were read
// The SKU is unique in product-service, so repeating the POST can never create a
// duplicate: at worst it answers 409, and then this check runs again.
import { listProducts, MAX_PAGE_SIZE } from '../api/products';
import type { ProductIn, ProductOut } from '../api/types';
import { matchesData } from './productRules';

/** At most this many pages of 100 matches are read */
export const RECOVERY_MAX_PAGES = 3;
/** product-service accepts q up to 100 characters; a prefix of the name still matches it */
const MAX_QUERY_LENGTH = 100;

export type CreateCheck =
  | { status: 'same'; product: ProductOut }
  | { status: 'different'; product: ProductOut }
  | { status: 'absent' }
  | { status: 'unknown'; reason: 'incomplete' | 'error'; error?: unknown };

export async function findCreatedProduct(data: ProductIn, signal?: AbortSignal): Promise<CreateCheck> {
  const q = data.name.slice(0, MAX_QUERY_LENGTH);
  let read = 0;
  try {
    for (let pageIndex = 0; pageIndex < RECOVERY_MAX_PAGES; pageIndex += 1) {
      const page = await listProducts({ q, includeInactive: true, limit: MAX_PAGE_SIZE, offset: pageIndex * MAX_PAGE_SIZE }, signal);
      const match = page.items.find((product) => product.sku === data.sku);
      if (match) return matchesData(match, data) ? { status: 'same', product: match } : { status: 'different', product: match };
      read += page.items.length;
      if (read >= page.total) return { status: 'absent' };
      if (page.items.length === 0) break; // the total changed while reading
    }
    return { status: 'unknown', reason: 'incomplete' };
  } catch (error) {
    if (signal?.aborted) throw error;
    return { status: 'unknown', reason: 'error', error };
  }
}
