// Public catalogue: active products of product-service, with search, category and
// pages, plus the availability of the shown products from inventory-service.
import { useEffect, useState, type FormEvent } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { listCategories, listProducts, type CategoryScan } from '../api/products';
import type { OffsetPage, ProductOut } from '../api/types';
import { availabilityOf, useStock } from '../catalog/availability';
import { categoryLabel } from '../catalog/categories';
import {
  MAX_SEARCH_LENGTH, PAGE_SIZE, pageOffset, readCatalogParams, toSearchParams, type CatalogParams,
} from '../catalog/params';
import { Alert, ErrorAlert } from '../components/Alert';
import { Button } from '../components/Button';
import { EmptyState } from '../components/EmptyState';
import { Pagination } from '../components/Pagination';
import { ProductCard } from '../components/ProductCard';
import { Spinner } from '../components/Spinner';

type ProductsResult =
  | { status: 'loading' }
  | { status: 'ready'; page: OffsetPage<ProductOut> }
  | { status: 'error'; error: unknown };

type CategoriesResult = { status: 'loading' } | { status: 'ready'; scan: CategoryScan } | { status: 'error' };

function queryString(params: Partial<CatalogParams>): string {
  const text = toSearchParams(params).toString();
  return text ? `?${text}` : '';
}

export function CatalogPage() {
  const [search, setSearch] = useSearchParams();
  const params = readCatalogParams(search);
  const { q, category, page } = params;

  const [draft, setDraft] = useState(q);
  useEffect(() => setDraft(q), [q]);

  const [attempt, setAttempt] = useState(0);
  const [products, setProducts] = useState<ProductsResult>({ status: 'loading' });
  useEffect(() => {
    const controller = new AbortController();
    setProducts({ status: 'loading' });
    listProducts(
      { q: q || undefined, category: category || undefined, limit: PAGE_SIZE, offset: pageOffset(page) },
      controller.signal,
    )
      .then((data) => {
        if (!controller.signal.aborted) setProducts({ status: 'ready', page: data });
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) setProducts({ status: 'error', error });
      });
    return () => controller.abort();
  }, [q, category, page, attempt]);

  const [categories, setCategories] = useState<CategoriesResult>({ status: 'loading' });
  useEffect(() => {
    const controller = new AbortController();
    listCategories(controller.signal)
      .then((scan) => {
        if (!controller.signal.aborted) setCategories({ status: 'ready', scan });
      })
      .catch(() => {
        // The catalogue still works without the category filter
        if (!controller.signal.aborted) setCategories({ status: 'error' });
      });
    return () => controller.abort();
  }, []);

  const shown = products.status === 'ready' ? products.page.items : [];
  const stock = useStock(shown.map((product) => product.id));

  const update = (next: Partial<CatalogParams>) => setSearch(toSearchParams({ ...params, ...next }));

  function onSearch(event: FormEvent) {
    event.preventDefault();
    update({ q: draft, page: 1 });
  }

  const categoryOptions =
    categories.status === 'ready'
      ? [...new Set([...categories.scan.categories, ...(category ? [category] : [])])]
      : category
        ? [category]
        : [];

  return (
    <section className="catalog">
      <header className="page-header">
        <h1>Catálogo</h1>
        <p className="page-header__lead">Productos activos de la tienda, con su precio y disponibilidad actuales.</p>
      </header>

      <form className="catalog-filters" role="search" onSubmit={onSearch}>
        <div className="field catalog-filters__search">
          <label htmlFor="catalog-q" className="field__label">
            Buscar por nombre
          </label>
          <div className="catalog-filters__row">
            <input
              id="catalog-q"
              type="search"
              className="field__input"
              value={draft}
              maxLength={MAX_SEARCH_LENGTH}
              onChange={(event) => setDraft(event.target.value)}
              placeholder="Por ejemplo: laptop"
            />
            <Button type="submit">Buscar</Button>
          </div>
        </div>
        {categories.status !== 'error' && (
          <div className="field catalog-filters__category">
            <label htmlFor="catalog-category" className="field__label">
              Categoría
            </label>
            <select
              id="catalog-category"
              className="field__input"
              value={category}
              disabled={categories.status === 'loading'}
              onChange={(event) => update({ category: event.target.value, page: 1 })}
            >
              <option value="">Todas las categorías</option>
              {categoryOptions.map((option) => (
                <option key={option} value={option}>
                  {categoryLabel(option)}
                </option>
              ))}
            </select>
            {categories.status === 'ready' && !categories.scan.complete && (
              <p className="field__hint">
                Categorías tomadas de los primeros {categories.scan.scanned} de {categories.scan.total} productos.
              </p>
            )}
          </div>
        )}
      </form>

      {products.status === 'loading' && (
        <div className="catalog__state">
          <Spinner label="Cargando productos…" />
        </div>
      )}

      {products.status === 'error' && (
        <div className="catalog__state">
          <ErrorAlert error={products.error} title="No pudimos cargar el catálogo." />
          <Button variant="secondary" onClick={() => setAttempt((n) => n + 1)}>
            Reintentar
          </Button>
        </div>
      )}

      {products.status === 'ready' && products.page.total === 0 && (
        <EmptyState
          title={q || category ? 'No encontramos productos con esos filtros' : 'Todavía no hay productos en el catálogo'}
          description={q || category ? 'Prueba con otra búsqueda o con otra categoría.' : undefined}
          action={
            q || category ? (
              <Link to="/productos" className="btn btn--secondary">
                Limpiar filtros
              </Link>
            ) : undefined
          }
        />
      )}

      {products.status === 'ready' && products.page.total > 0 && products.page.items.length === 0 && (
        <EmptyState
          title="Esta página no existe"
          description="La búsqueda tiene menos páginas."
          action={
            <Link to={`/productos${queryString({ ...params, page: 1 })}`} className="btn btn--secondary">
              Ir a la primera página
            </Link>
          }
        />
      )}

      {products.status === 'ready' && products.page.items.length > 0 && (
        <>
          <p className="catalog__summary">
            {products.page.total} {products.page.total === 1 ? 'producto' : 'productos'}
            {q && <> para «{q}»</>}
            {category && <> en {categoryLabel(category)}</>}
          </p>
          {stock.status === 'error' && (
            <Alert tone="warning">
              <p>No pudimos consultar la disponibilidad en este momento. Los productos se pueden agregar al carrito; las existencias se confirman al comprar.</p>
            </Alert>
          )}
          <div className="product-grid">
            {products.page.items.map((product) => (
              <ProductCard key={product.id} product={product} availability={availabilityOf(product.id, stock)} />
            ))}
          </div>
          <Pagination
            page={page}
            totalPages={Math.max(1, Math.ceil(products.page.total / PAGE_SIZE))}
            hrefFor={(target) => `/productos${queryString({ ...params, page: target })}`}
          />
        </>
      )}
    </section>
  );
}
