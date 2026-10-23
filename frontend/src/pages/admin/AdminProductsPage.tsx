// Products for an admin: active and inactive, search by name, 20 per page. The stock of
// the page comes from ONE GET /api/inventory?ids= (with the admin token it includes the
// reserved units). A product missing from that answer has no stock record, which is not
// the same as 0 units; if inventory-service fails, the products are still listed.
import { useEffect, useState, type FormEvent } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { getStock } from '../../api/inventory';
import { listProducts } from '../../api/products';
import type { OffsetPage, ProductOut, StockAdmin } from '../../api/types';
import { categoryLabel } from '../../catalog/categories';
import { MAX_SEARCH_LENGTH, readCatalogParams } from '../../catalog/params';
import { Alert, ErrorAlert } from '../../components/Alert';
import { Button } from '../../components/Button';
import { EmptyState } from '../../components/EmptyState';
import { Pagination } from '../../components/Pagination';
import { Spinner } from '../../components/Spinner';
import { StatusBadge } from '../../components/StatusBadge';
import { formatMoney } from '../../utils/format';

export const ADMIN_PRODUCTS_PAGE_SIZE = 20;

type Result = { status: 'loading' } | { status: 'ready'; page: OffsetPage<ProductOut> } | { status: 'error'; error: unknown };
type StockState = { key: string; status: 'loading' | 'ready' | 'error'; rows: Map<number, StockAdmin> };

function hrefFor(page: number, q: string): string {
  const search = new URLSearchParams();
  if (q) search.set('q', q);
  if (page > 1) search.set('pagina', String(page));
  const query = search.toString();
  return query ? `/admin/productos?${query}` : '/admin/productos';
}

export function AdminProductsPage() {
  const [search, setSearch] = useSearchParams();
  const { q, page } = readCatalogParams(search);
  const [draft, setDraft] = useState(q);
  const [attempt, setAttempt] = useState(0);
  const [result, setResult] = useState<Result>({ status: 'loading' });
  const [stock, setStock] = useState<StockState>({ key: '', status: 'loading', rows: new Map() });

  useEffect(() => setDraft(q), [q]);

  useEffect(() => {
    const controller = new AbortController();
    setResult({ status: 'loading' });
    listProducts(
      { q: q || undefined, includeInactive: true, limit: ADMIN_PRODUCTS_PAGE_SIZE, offset: (page - 1) * ADMIN_PRODUCTS_PAGE_SIZE },
      controller.signal,
    )
      .then((data) => {
        if (!controller.signal.aborted) setResult({ status: 'ready', page: data });
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) setResult({ status: 'error', error });
      });
    return () => controller.abort();
  }, [q, page, attempt]);

  // One stock request for the products of this page
  const ids = result.status === 'ready' ? result.page.items.map((p) => p.id) : [];
  const stockKey = ids.join(',');
  useEffect(() => {
    if (!stockKey) return undefined;
    const controller = new AbortController();
    setStock({ key: stockKey, status: 'loading', rows: new Map() });
    getStock(stockKey.split(',').map(Number), controller.signal)
      .then((rows) => {
        if (!controller.signal.aborted) {
          setStock({ key: stockKey, status: 'ready', rows: new Map((rows as StockAdmin[]).map((r) => [r.product_id, r])) });
        }
      })
      .catch(() => {
        if (!controller.signal.aborted) setStock({ key: stockKey, status: 'error', rows: new Map() });
      });
    return () => controller.abort();
  }, [stockKey]);
  const stockNow: StockState = stock.key === stockKey ? stock : { key: stockKey, status: 'loading', rows: new Map() };

  function onSearch(event: FormEvent) {
    event.preventDefault();
    const next = new URLSearchParams();
    const text = draft.trim().slice(0, MAX_SEARCH_LENGTH);
    if (text) next.set('q', text);
    setSearch(next);
  }

  return (
    <section className="admin-products">
      <nav className="breadcrumb" aria-label="Ruta">
        <Link to="/admin">Administración</Link>
        <span aria-hidden="true">/</span>
        <span>Productos</span>
      </nav>
      <header className="page-header page-header--row">
        <div>
          <h1>Productos</h1>
          <p className="page-header__lead">Activos e inactivos. Los inactivos no aparecen en el catálogo.</p>
        </div>
        <Link to="/admin/productos/nuevo" className="btn btn--primary">
          Nuevo producto
        </Link>
      </header>

      <form className="catalog-filters admin-products__search" role="search" onSubmit={onSearch}>
        <div className="field catalog-filters__search">
          <label htmlFor="admin-products-q" className="field__label">
            Buscar por nombre
          </label>
          <div className="catalog-filters__row">
            <input
              id="admin-products-q"
              type="search"
              className="field__input"
              value={draft}
              maxLength={MAX_SEARCH_LENGTH}
              onChange={(event) => setDraft(event.target.value)}
            />
            <Button type="submit">Buscar</Button>
          </div>
        </div>
      </form>

      {result.status === 'loading' && (
        <div className="catalog__state">
          <Spinner label="Cargando productos…" />
        </div>
      )}
      {result.status === 'error' && (
        <div className="catalog__state">
          <ErrorAlert error={result.error} title="No pudimos cargar los productos." />
          <Button variant="secondary" onClick={() => setAttempt((n) => n + 1)}>
            Reintentar
          </Button>
        </div>
      )}
      {result.status === 'ready' && result.page.total === 0 && (
        <EmptyState title={q ? `Ningún producto coincide con «${q}»` : 'Todavía no hay productos'} />
      )}
      {result.status === 'ready' && result.page.total > 0 && result.page.items.length === 0 && (
        <EmptyState
          title="Esta página no existe"
          action={
            <Link to={hrefFor(1, q)} className="btn btn--secondary">
              Ir a la primera página
            </Link>
          }
        />
      )}
      {result.status === 'ready' && result.page.items.length > 0 && (
        <>
          {stockNow.status === 'error' && (
            <Alert tone="info">
              <p>Las existencias no están disponibles en este momento; los productos se muestran igual.</p>
            </Alert>
          )}
          <div className="admin-table">
            <table>
              <thead>
                <tr>
                  <th scope="col">Producto</th>
                  <th scope="col">Categoría</th>
                  <th scope="col" className="num">Precio</th>
                  <th scope="col">Estado</th>
                  <th scope="col" className="num">Existencias</th>
                </tr>
              </thead>
              <tbody>
                {result.page.items.map((product) => (
                  <tr key={product.id}>
                    <td data-label="Producto">
                      <Link to={`/admin/productos/${product.id}`} className="admin-table__name">
                        {product.name}
                      </Link>
                      <span className="admin-table__sku">{product.sku}</span>
                    </td>
                    <td data-label="Categoría">{categoryLabel(product.category)}</td>
                    <td data-label="Precio" className="num">
                      <span className="price">{formatMoney(product.price)}</span>
                    </td>
                    <td data-label="Estado">
                      <StatusBadge tone={product.is_active ? 'success' : 'neutral'} label={product.is_active ? 'Activo' : 'Inactivo'} />
                    </td>
                    <td data-label="Existencias" className="num">
                      <StockCell state={stockNow} productId={product.id} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pagination
            page={page}
            totalPages={Math.max(1, Math.ceil(result.page.total / ADMIN_PRODUCTS_PAGE_SIZE))}
            hrefFor={(target) => hrefFor(target, q)}
          />
        </>
      )}
    </section>
  );
}

function StockCell({ state, productId }: { state: StockState; productId: number }) {
  if (state.status === 'loading') return <span className="admin-table__muted">…</span>;
  if (state.status === 'error') return <span className="admin-table__muted">No disponible</span>;
  const row = state.rows.get(productId);
  if (!row) return <span className="admin-table__muted">Sin registro</span>;
  return (
    <span>
      {row.available} disp.
      {row.reserved > 0 && <span className="admin-table__muted"> · {row.reserved} reserv.</span>}
    </span>
  );
}
