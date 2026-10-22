import { Link } from 'react-router-dom';
import type { ProductOut } from '../api/types';
import type { Availability } from '../catalog/availability';
import { categoryLabel } from '../catalog/categories';
import { formatMoney } from '../utils/format';
import { StockLabel } from './StockLabel';

/** The backend has no product images: a neutral mark with the category initial is shown instead */
export function CategoryMark({ category, large = false }: { category: string; large?: boolean }) {
  return (
    <div className={`category-mark${large ? ' category-mark--large' : ''}`} aria-hidden="true">
      {categoryLabel(category).charAt(0)}
    </div>
  );
}

export function ProductCard({ product, availability }: { product: ProductOut; availability: Availability }) {
  return (
    <article className="product-card">
      <CategoryMark category={product.category} />
      <div className="product-card__body">
        <p className="product-card__category">{categoryLabel(product.category)}</p>
        <h2 className="product-card__name">
          <Link to={`/productos/${product.id}`}>{product.name}</Link>
        </h2>
        <p className="product-card__sku">SKU {product.sku}</p>
      </div>
      <div className="product-card__footer">
        <span className="price">{formatMoney(product.price)}</span>
        <StockLabel availability={availability} />
      </div>
    </article>
  );
}
