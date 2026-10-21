from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Product
from app.schemas import ProductIn, ProductUpdate


class ProductNotFound(Exception):
    pass


class SkuAlreadyExists(Exception):
    pass


def list_products(
    db: Session,
    *,
    category: str | None = None,
    search: str | None = None,
    ids: list[int] | None = None,
    include_inactive: bool = False,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[Product], int]:
    """Returns one page of products and the total number that match the filters."""
    query = select(Product)
    if not include_inactive:
        query = query.where(Product.is_active.is_(True))
    if category:
        query = query.where(Product.category == category)
    if search:
        # Escape the LIKE wildcards so "%" or "_" typed by a user are literal
        escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        query = query.where(Product.name.ilike(f"%{escaped}%", escape="\\"))
    if ids:
        query = query.where(Product.id.in_(ids))

    total = db.scalar(select(func.count()).select_from(query.subquery()))
    items = db.scalars(query.order_by(Product.id).limit(limit).offset(offset)).all()
    return list(items), total


def get_product(db: Session, product_id: int, *, include_inactive: bool = False) -> Product:
    product = db.get(Product, product_id)
    if product is None or (not product.is_active and not include_inactive):
        raise ProductNotFound
    return product


def _save(db: Session, product: Product) -> Product:
    try:
        db.commit()
    except IntegrityError as exc:
        # The unique index on sku also protects against simultaneous requests
        db.rollback()
        raise SkuAlreadyExists from exc
    db.refresh(product)
    return product


def create_product(db: Session, data: ProductIn) -> Product:
    product = Product(**data.model_dump())
    db.add(product)
    return _save(db, product)


def update_product(db: Session, product_id: int, data: ProductUpdate) -> Product:
    product = get_product(db, product_id, include_inactive=True)
    for field, value in data.model_dump().items():
        setattr(product, field, value)
    return _save(db, product)


def deactivate_product(db: Session, product_id: int) -> None:
    """Logical delete: past orders still reference this product."""
    product = get_product(db, product_id, include_inactive=True)
    product.is_active = False
    db.commit()
