import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import Reservation, ReservationItem, Stock, StockAdjustment

# Product ids far from the real catalog, used only inside these tests
P1, P2 = 900_001, 900_002


def assert_rejected(db_session, obj):
    db_session.add(obj)
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_available_cannot_be_negative(db_session):
    assert_rejected(db_session, Stock(product_id=P1, available=-1))


def test_reserved_cannot_be_negative(db_session):
    assert_rejected(db_session, Stock(product_id=P1, available=1, reserved=-1))


def test_reservation_status_must_be_valid(db_session):
    assert_rejected(db_session, Reservation(order_id=uuid.uuid4(), status="LOST"))


def test_one_reservation_per_order(db_session):
    order_id = uuid.uuid4()
    db_session.add(Reservation(order_id=order_id, status="RESERVED"))
    db_session.flush()

    assert_rejected(db_session, Reservation(order_id=order_id, status="RESERVED"))


def test_reserved_quantity_must_be_positive(db_session):
    reservation = Reservation(order_id=uuid.uuid4(), status="RESERVED",
                              items=[ReservationItem(product_id=P1, quantity=0)])

    assert_rejected(db_session, reservation)


def test_product_cannot_repeat_in_the_same_reservation(db_session):
    reservation = Reservation(order_id=uuid.uuid4(), status="RESERVED",
                              items=[ReservationItem(product_id=P1, quantity=1),
                                     ReservationItem(product_id=P1, quantity=2)])

    assert_rejected(db_session, reservation)


def test_adjustment_cannot_be_zero(db_session):
    assert_rejected(db_session, StockAdjustment(idempotency_key="k-zero", product_id=P1, delta=0,
                                                available_before=1, available_after=1))


def test_adjustment_key_is_unique(db_session):
    values = dict(product_id=P2, delta=5, available_before=0, available_after=5)
    db_session.add(StockAdjustment(idempotency_key="k-dup", **values))
    db_session.flush()

    assert_rejected(db_session, StockAdjustment(idempotency_key="k-dup", **values))
