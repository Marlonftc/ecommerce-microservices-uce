"""Simultaneous requests against the real database.

These tests cannot use the rollback session: each thread needs its own
connection, as real requests do. They use product ids far from the real
catalog and delete everything they create.
"""
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import delete, select

from app.database import SessionLocal, engine
from app.models import Reservation, Stock, StockAdjustment
from app.schemas import ReservationItemIn
from app.services import reservation_service, stock_service

A, B = 900_101, 900_102


@pytest.fixture
def real_db():
    try:
        engine.connect().close()
    except Exception:
        pytest.skip("database not available")
    created_orders: list[uuid.UUID] = []
    yield created_orders
    with SessionLocal() as db:
        db.execute(delete(Reservation).where(Reservation.order_id.in_(created_orders)))
        db.execute(delete(StockAdjustment).where(StockAdjustment.product_id.in_([A, B])))
        db.execute(delete(Stock).where(Stock.product_id.in_([A, B])))
        db.commit()


def set_stock(**units):
    with SessionLocal() as db:
        db.execute(delete(Stock).where(Stock.product_id.in_([A, B])))
        for product_id, available in units.items():
            db.add(Stock(product_id={"a": A, "b": B}[product_id], available=available))
        db.commit()


def read_stock(product_id):
    with SessionLocal() as db:
        s = db.get(Stock, product_id)
        return s.available, s.reserved


def run_at_the_same_time(task, count):
    """Starts all the threads together, so the requests really overlap."""
    barrier = threading.Barrier(count)

    def wrapped(index):
        barrier.wait()
        return task(index)

    with ThreadPoolExecutor(max_workers=count) as pool:
        return list(pool.map(wrapped, range(count)))


def try_reserve(order_id, items):
    with SessionLocal() as db:
        try:
            reservation_service.reserve(db, order_id, items)
            return "ok"
        except reservation_service.InsufficientStock:
            return "no stock"


def test_ten_buyers_for_the_last_unit_only_one_wins(real_db):
    set_stock(a=1)
    orders = [uuid.uuid4() for _ in range(10)]
    real_db.extend(orders)
    items = [ReservationItemIn(product_id=A, quantity=1)]

    results = run_at_the_same_time(lambda i: try_reserve(orders[i], items), 10)

    assert results.count("ok") == 1
    assert results.count("no stock") == 9
    assert read_stock(A) == (0, 1)  # never negative, never oversold


def test_opposite_product_order_does_not_deadlock(real_db):
    set_stock(a=100, b=100)
    orders = [uuid.uuid4() for _ in range(20)]
    real_db.extend(orders)
    ab = [ReservationItemIn(product_id=A, quantity=1), ReservationItemIn(product_id=B, quantity=1)]
    ba = list(reversed(ab))

    results = run_at_the_same_time(lambda i: try_reserve(orders[i], ab if i % 2 else ba), 20)

    assert results.count("ok") == 20
    assert read_stock(A) == (80, 20)
    assert read_stock(B) == (80, 20)


def test_same_order_sent_five_times_at_once_reserves_once(real_db):
    set_stock(a=10)
    order_id = uuid.uuid4()
    real_db.append(order_id)
    items = [ReservationItemIn(product_id=A, quantity=3)]

    run_at_the_same_time(lambda i: try_reserve(order_id, items), 5)

    assert read_stock(A) == (7, 3)


def test_same_adjustment_key_sent_five_times_at_once_applies_once(real_db):
    set_stock(a=10)
    key = f"concurrency-{uuid.uuid4()}"

    def task(_):
        with SessionLocal() as db:
            stock_service.adjust_stock(db, product_id=A, delta=10, reason="", admin_user_id=1,
                                       idempotency_key=key, product_exists=lambda _: True)

    run_at_the_same_time(task, 5)

    assert read_stock(A) == (20, 0)
    with SessionLocal() as db:
        assert len(db.scalars(select(StockAdjustment).where(StockAdjustment.idempotency_key == key)).all()) == 1
