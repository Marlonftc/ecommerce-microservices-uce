// Catalogue filters live in the URL (?q=&categoria=&pagina=), so a reload, the back
// button or a shared link show the same results. Invalid values fall back to defaults.

export const PAGE_SIZE = 12;
export const MAX_SEARCH_LENGTH = 100; // q max_length in product-service
export const MAX_CATEGORY_LENGTH = 50;
const MAX_PAGE = 10_000;
const MAX_PRODUCT_ID = 2_147_483_647;

export interface CatalogParams {
  q: string;
  category: string;
  page: number;
}

export function readCatalogParams(search: URLSearchParams): CatalogParams {
  const q = (search.get('q') ?? '').trim().slice(0, MAX_SEARCH_LENGTH);
  const category = (search.get('categoria') ?? '').trim().slice(0, MAX_CATEGORY_LENGTH);
  const rawPage = (search.get('pagina') ?? '').trim();
  const number = /^\d{1,6}$/.test(rawPage) ? Number(rawPage) : 1;
  return { q, category, page: number >= 1 && number <= MAX_PAGE ? number : 1 };
}

/** Builds the query string; defaults (empty filters, page 1) are left out */
export function toSearchParams(params: Partial<CatalogParams>): URLSearchParams {
  const search = new URLSearchParams();
  const q = params.q?.trim().slice(0, MAX_SEARCH_LENGTH);
  if (q) search.set('q', q);
  if (params.category) search.set('categoria', params.category);
  if (params.page && params.page > 1) search.set('pagina', String(params.page));
  return search;
}

export function pageOffset(page: number): number {
  return (page - 1) * PAGE_SIZE;
}

/** Product id from the URL: a positive 32-bit integer, or null (no request is sent) */
export function parseProductId(raw: string | undefined): number | null {
  if (!raw || !/^[1-9]\d{0,9}$/.test(raw)) return null;
  const id = Number(raw);
  return id <= MAX_PRODUCT_ID ? id : null;
}
