// Product page: data from product-service, availability from inventory-service and
// the "add to cart" form, which needs a session.
import { useEffect, useState, type FormEvent } from 'react';
import { Link, useLocation, useParams } from 'react-router-dom';
import { ApiError } from '../api/errors';
import { getProduct } from '../api/products';
import type { ProductOut } from '../api/types';
import { useAuth } from '../auth/AuthContext';
import { useCart } from '../cart/CartContext';
import { CART_MESSAGES, describeProblem, isSilent } from '../cart/cartErrors';
import { parseQuantity, unitsLeftToAdd } from '../cart/quantity';
import { availabilityOf, canAddToCart, knownUnits, useStock, type Availability } from '../catalog/availability';
import { categoryLabel } from '../catalog/categories';
import { parseProductId, toSearchParams } from '../catalog/params';
import { Alert, ErrorAlert } from '../components/Alert';
import { Button } from '../components/Button';
import { CategoryMark } from '../components/ProductCard';
import { QuantityInput } from '../components/QuantityInput';
import { Spinner } from '../components/Spinner';
import { StockLabel } from '../components/StockLabel';
import { formatMoney, sameAmount } from '../utils/format';
import { NotFoundPage } from './NotFoundPage';

type ProductResult = { status: 'loading' } | { status: 'ready'; product: ProductOut } | { status: 'error'; error: unknown };

export function ProductDetailPage() {
  const id = parseProductId(useParams().productId);
  if (id === null) return <NotFoundPage />; // no request for an invalid id
  return <ProductDetail key={id} id={id} />;
}

function ProductDetail({ id }: { id: number }) {
  const [attempt, setAttempt] = useState(0);
  const [result, setResult] = useState<ProductResult>({ status: 'loading' });

  // product-service answers by role (an admin also sees inactive products), so the product
  // is requested only once the stored session was restored, and again if the user changes
  const { status: authStatus, user } = useAuth();
  const viewer = authStatus === 'loading' ? undefined : (user?.id ?? null);

  useEffect(() => {
    if (viewer === undefined) return undefined;
    const controller = new AbortController();
    // A refresh keeps the page on screen; only the first load shows the spinner
    setResult((previous) => (previous.status === 'ready' ? previous : { status: 'loading' }));
    getProduct(id, controller.signal)
      .then((product) => {
        if (!controller.signal.aborted) setResult({ status: 'ready', product });
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) setResult({ status: 'error', error });
      });
    return () => controller.abort();
  }, [id, attempt, viewer]);

  const stock = useStock([id]);
  const refresh = () => setAttempt((n) => n + 1);

  if (result.status === 'loading') {
    return (
      <div className="catalog__state">
        <Spinner label="Cargando producto…" />
      </div>
    );
  }

  if (result.status === 'error') {
    if (result.error instanceof ApiError && result.error.status === 404) {
      return (
        <section className="not-found">
          <h1>Producto no encontrado</h1>
          <p>El producto no existe o ya no está disponible en el catálogo.</p>
          <Link to="/productos" className="btn btn--secondary">
            Volver al catálogo
          </Link>
        </section>
      );
    }
    return (
      <section className="catalog__state">
        <ErrorAlert error={result.error} title="No pudimos cargar el producto." />
        <div className="form-actions">
          <Button variant="secondary" onClick={refresh}>
            Reintentar
          </Button>
          <Link to="/productos" className="btn btn--ghost">
            Volver al catálogo
          </Link>
        </div>
      </section>
    );
  }

  const { product } = result;
  const availability = availabilityOf(product.id, stock);
  const label = categoryLabel(product.category);

  return (
    <article className="product-detail">
      <nav className="breadcrumb" aria-label="Ruta">
        <Link to="/productos">Catálogo</Link>
        <span aria-hidden="true">/</span>
        <Link to={`/productos?${toSearchParams({ category: product.category }).toString()}`}>{label}</Link>
      </nav>
      <div className="product-detail__layout">
        <CategoryMark category={product.category} large />
        <div className="product-detail__info">
          <p className="product-card__category">{label}</p>
          <h1>{product.name}</h1>
          <p className="product-card__sku">SKU {product.sku}</p>
          <p className="price price--large">{formatMoney(product.price)}</p>
          <StockLabel availability={availability} />
          {product.description && <p className="product-detail__description">{product.description}</p>}
          <PurchaseBox product={product} availability={availability} onProductChanged={refresh} />
        </div>
      </div>
    </article>
  );
}

interface Feedback {
  tone: 'success' | 'warning' | 'error';
  text: string;
  cartLink?: boolean;
}

const BLOCKED_MESSAGES: Partial<Record<Availability['state'], string>> = {
  'sold-out': 'Este producto está agotado, por eso no se puede agregar al carrito.',
  'no-record': 'Este producto no tiene existencias registradas, por eso no se puede agregar al carrito.',
};

function PurchaseBox({
  product,
  availability,
  onProductChanged,
}: {
  product: ProductOut;
  availability: Availability;
  /** Reads the product again (new price, or deactivated) */
  onProductChanged(): void;
}) {
  const { status } = useAuth();
  const cart = useCart();
  const location = useLocation();
  const [units, setUnits] = useState('1');
  const [feedback, setFeedback] = useState<Feedback | null>(null);
  const [priceNotice, setPriceNotice] = useState<string | null>(null);

  // Inactive products cannot be bought, even by an admin who is allowed to see them
  if (!product.is_active) {
    return (
      <Alert tone="warning">
        <p>Este producto está inactivo y no se puede agregar al carrito.</p>
      </Alert>
    );
  }

  if (status === 'anonymous') {
    return (
      <div className="purchase-box">
        <p>Inicia sesión para agregar productos a tu carrito.</p>
        <Link to="/login" state={{ from: location.pathname }} className="btn btn--primary">
          Inicia sesión para comprar
        </Link>
      </div>
    );
  }
  if (status !== 'authenticated') return null;

  const inCart = cart.quantityOf(product.id);
  const left = unitsLeftToAdd(inCart, knownUnits(availability));
  const addable = canAddToCart(availability);
  const check = parseQuantity(units, left);
  const locked = cart.cart?.locked === true;
  const blocked = BLOCKED_MESSAGES[availability.state];

  async function onAdd(event: FormEvent) {
    event.preventDefault();
    if (!check.ok) return;
    setFeedback(null);
    setPriceNotice(null);
    const result = await cart.addItem(product.id, check.value);
    if (result.ok) {
      const line = result.cart.items.find((item) => item.product_id === product.id);
      const total = line?.quantity ?? inCart + check.value;
      setFeedback({ tone: 'success', text: `Producto agregado. Tienes ${total} en tu carrito.`, cartLink: true });
      setUnits('1');
      if (line?.unit_price && !sameAmount(line.unit_price, product.price)) {
        setPriceNotice(`El precio de este producto cambió a ${formatMoney(line.unit_price)}. Tu carrito usa el precio actual del catálogo.`);
        onProductChanged();
      }
      return;
    }
    if (isSilent(result.problem)) return;
    const tone = result.problem.kind === 'uncertain' || result.problem.kind === 'changed' ? 'warning' : 'error';
    setFeedback({ tone, text: describeProblem(result.problem, result.cart !== null) });
    if (result.problem.kind === 'product-unavailable') onProductChanged();
  }

  return (
    <form className="purchase-box" onSubmit={onAdd} noValidate>
      {availability.state === 'unknown' && (
        <Alert tone="warning">
          <p>No pudimos verificar la disponibilidad. Puedes agregarlo al carrito; las existencias se confirman al realizar la compra.</p>
        </Alert>
      )}
      {locked && (
        <Alert tone="warning">
          <p>{CART_MESSAGES.locked}</p>
        </Alert>
      )}
      {cart.needsReload && (
        <Alert tone="warning">
          <p>{cart.refreshing ? 'Consultando tu carrito…' : CART_MESSAGES['needs-reload']}</p>
          {!cart.refreshing && (
            <Button variant="secondary" onClick={() => void cart.reload()}>
              Reintentar
            </Button>
          )}
        </Alert>
      )}
      {inCart > 0 && <p className="purchase-box__in-cart">Ya tienes {inCart} en tu carrito.</p>}

      {blocked ? (
        <p className="purchase-box__blocked">{blocked}</p>
      ) : addable && left === 0 ? (
        <p className="purchase-box__blocked">Ya tienes en tu carrito la cantidad máxima que se puede agregar de este producto.</p>
      ) : (
        <div className="purchase-box__row">
          <QuantityInput
            id="purchase-units"
            label="Cantidad"
            value={units}
            onChange={setUnits}
            max={Math.max(1, left)}
            disabled={cart.busy || locked}
            error={!check.ok ? check.message : undefined}
          />
          <Button
            type="submit"
            loading={cart.busy}
            disabled={!addable || !check.ok || locked || cart.needsReload || cart.status === 'loading'}
          >
            Agregar al carrito
          </Button>
        </div>
      )}

      {feedback && (
        <Alert tone={feedback.tone}>
          <p>{feedback.text}</p>
          {feedback.cartLink && <Link to="/carrito">Ver carrito</Link>}
        </Alert>
      )}
      {priceNotice && (
        <Alert tone="warning">
          <p>{priceNotice}</p>
        </Alert>
      )}
    </form>
  );
}
