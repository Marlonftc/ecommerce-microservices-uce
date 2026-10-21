from decimal import Decimal

from sqlalchemy import func, select

from app.models import Product
from app.seed import INITIAL_PRODUCTS, seed_products


def test_seed_loads_the_initial_catalog_once(db_session):
    seed_products(db_session)
    seed_products(db_session)  # running it again must not duplicate anything

    ids = db_session.scalars(select(Product.id).where(Product.id <= 8).order_by(Product.id)).all()
    assert ids == [p["id"] for p in INITIAL_PRODUCTS]


def test_seed_does_not_overwrite_admin_changes(db_session):
    seed_products(db_session)
    db_session.get(Product, 1).price = Decimal("699.99")
    db_session.commit()

    seed_products(db_session)

    assert db_session.get(Product, 1).price == Decimal("699.99")


def test_new_product_gets_an_id_after_the_initial_ones(db_session):
    seed_products(db_session)

    product = Product(sku="NEW-001", name="Producto nuevo", category="accesorios", price=Decimal("5.00"))
    db_session.add(product)
    db_session.commit()

    assert product.id > max(p["id"] for p in INITIAL_PRODUCTS)


def test_prices_are_exact_decimals(db_session):
    seed_products(db_session)

    total = db_session.scalar(select(func.sum(Product.price)).where(Product.id.in_([4, 5, 8])))

    # 59.99 + 19.50 + 9.99 with float would not be exact
    assert total == Decimal("89.48")
