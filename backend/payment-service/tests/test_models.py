import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import Payment


def payment(**changes) -> Payment:
    values = dict(order_id=uuid.uuid4(), idempotency_key=f"key-{uuid.uuid4()}", user_id=1,
                  amount=Decimal("100.00"), simulation="APPROVE")
    values.update(changes)
    return Payment(**values)


def assert_rejected(db_session, obj):
    db_session.add(obj)
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_new_payment_starts_pending_in_usd(db_session):
    p = payment()
    db_session.add(p)
    db_session.flush()
    db_session.refresh(p)

    assert (p.status, p.currency, p.processed_at) == ("PENDING", "USD", None)


def test_amount_must_be_positive(db_session):
    assert_rejected(db_session, payment(amount=Decimal("0")))
    assert_rejected(db_session, payment(amount=Decimal("-5.00")))


def test_only_usd_is_accepted(db_session):
    assert_rejected(db_session, payment(currency="EUR"))


def test_status_must_be_valid(db_session):
    assert_rejected(db_session, payment(status="PAID"))


def test_simulation_must_be_valid(db_session):
    assert_rejected(db_session, payment(simulation="MAYBE"))


def test_one_payment_per_order(db_session):
    order_id = uuid.uuid4()
    db_session.add(payment(order_id=order_id))
    db_session.flush()

    assert_rejected(db_session, payment(order_id=order_id))


def test_idempotency_key_is_unique(db_session):
    db_session.add(payment(idempotency_key="same-key"))
    db_session.flush()

    assert_rejected(db_session, payment(idempotency_key="same-key"))


def test_resolved_payment_needs_a_processing_date(db_session):
    assert_rejected(db_session, payment(status="APPROVED", processed_at=None))


def test_pending_payment_cannot_have_a_processing_date(db_session):
    assert_rejected(db_session, payment(status="PENDING", processed_at=datetime.now(UTC)))


def test_database_rounds_extra_decimals_silently(db_session):
    # PostgreSQL rounds 10.999 to 11.00 without an error. This is why the API
    # must reject amounts with more than 2 decimals before they reach the database.
    p = payment(amount=Decimal("10.999"))
    db_session.add(p)
    db_session.flush()
    db_session.refresh(p)

    assert p.amount == Decimal("11.00")
