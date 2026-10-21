import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.models import Notification


def notification(**changes) -> Notification:
    order_id = uuid.uuid4()
    values = dict(event_id=f"{order_id}:ORDER_PAID", user_id=7, order_id=order_id,
                  type="ORDER_PAID", amount=Decimal("149.90"), currency="USD",
                  title="Pago confirmado", message="Tu pedido fue pagado.")
    values.update(changes)
    return Notification(**values)


def assert_rejected(db_session, obj, error=IntegrityError):
    db_session.add(obj)
    with pytest.raises(error):
        db_session.flush()
    db_session.rollback()


def test_new_notification_starts_unread(db_session):
    n = notification()
    db_session.add(n)
    db_session.flush()
    db_session.refresh(n)

    assert (n.is_read, n.read_at) == (False, None)
    assert isinstance(n.id, uuid.UUID)
    assert n.created_at.utcoffset() is not None  # stored with time zone


def test_event_id_is_unique(db_session):
    db_session.add(notification(event_id="same-event"))
    db_session.flush()

    assert_rejected(db_session, notification(event_id="same-event"))


def test_event_id_longer_than_100_is_rejected(db_session):
    assert_rejected(db_session, notification(event_id="x" * 101), DBAPIError)


@pytest.mark.parametrize("kind", ["ORDER_PAID", "PAYMENT_REJECTED", "ORDER_CANCELLED"])
def test_the_three_types_are_accepted(db_session, kind):
    db_session.add(notification(type=kind))
    db_session.flush()


def test_other_types_are_rejected(db_session):
    assert_rejected(db_session, notification(type="ORDER_SHIPPED"))
    assert_rejected(db_session, notification(type="order_paid"))


def test_amount_and_currency_can_both_be_empty(db_session):
    db_session.add(notification(type="ORDER_CANCELLED", amount=None, currency=None))
    db_session.flush()


@pytest.mark.parametrize("amount, currency", [
    (Decimal("10.00"), None),   # amount without currency
    (None, "USD"),              # currency without amount (CHECK must not evaluate to NULL)
    (Decimal("0"), "USD"),
    (Decimal("-5.00"), "USD"),
    (Decimal("10.00"), "EUR"),
])
def test_invalid_amount_and_currency_are_rejected(db_session, amount, currency):
    assert_rejected(db_session, notification(amount=amount, currency=currency))


def test_postgres_rounds_extra_decimals_silently(db_session):
    # Documents why precision must be validated before reaching the database
    n = notification(amount=Decimal("10.005"))
    db_session.add(n)
    db_session.flush()
    db_session.refresh(n)

    assert n.amount == Decimal("10.01")


def test_read_state_and_read_date_must_agree(db_session):
    assert_rejected(db_session, notification(is_read=True, read_at=None))
    assert_rejected(db_session, notification(is_read=False, read_at=datetime.now(UTC)))

    db_session.add(notification(is_read=True, read_at=datetime.now(UTC)))
    db_session.flush()


@pytest.mark.parametrize("field", ["event_id", "user_id", "order_id", "type", "title", "message"])
def test_required_fields(db_session, field):
    assert_rejected(db_session, notification(**{field: None}))


def test_inbox_index_exists(db_session):
    indexes = inspect(db_session.connection()).get_indexes("notifications")

    assert {"name": "ix_notifications_user_id_created_at",
            "column_names": ["user_id", "created_at"]}.items() <= next(
        i for i in indexes if i["name"] == "ix_notifications_user_id_created_at").items()
