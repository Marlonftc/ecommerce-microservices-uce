from sqlalchemy import select, update

from app.models import Stock, StockAdjustment
from app.seed import INITIAL_STOCK, seed_stock

INITIAL_IDS = [s["product_id"] for s in INITIAL_STOCK]


def test_seed_creates_the_initial_stock_once(db_session):
    seed_stock(db_session)
    seed_stock(db_session)

    stock = db_session.scalars(select(Stock).where(Stock.product_id.in_(INITIAL_IDS))).all()
    assert len(stock) == len(INITIAL_STOCK)


def test_seed_matches_the_initial_catalog_ids(db_session):
    # Same ids and skus as the initial catalog of product-service
    expected = {1: "LAP-001", 2: "LAP-002", 3: "MON-001", 4: "TEC-001",
                5: "MOU-001", 6: "AUD-001", 7: "SSD-001", 8: "USB-001"}

    assert {s["product_id"]: s["sku"] for s in INITIAL_STOCK} == expected


def test_seed_does_not_overwrite_later_movements(db_session):
    seed_stock(db_session)
    db_session.execute(update(Stock).where(Stock.product_id == 1).values(available=3, reserved=2))
    db_session.commit()

    seed_stock(db_session)

    stock = db_session.get(Stock, 1)
    assert (stock.available, stock.reserved) == (3, 2)


def test_seed_leaves_one_history_entry_per_product(db_session):
    seed_stock(db_session)
    seed_stock(db_session)

    keys = db_session.scalars(
        select(StockAdjustment.idempotency_key).where(StockAdjustment.idempotency_key.like("seed-%"))
    ).all()
    assert sorted(keys) == sorted(f"seed-{i}" for i in INITIAL_IDS)
