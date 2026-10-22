import uuid
from decimal import Decimal

import pytest

from app.clients import notification_client as nc


def test_event_id_has_the_agreed_format():
    order_id = uuid.uuid4()

    assert nc.event_id(order_id, "ORDER_PAID") == f"{order_id}:ORDER_PAID"


def test_send_paid_event(fake):
    order_id = uuid.uuid4()
    service = fake(nc, (201, {}))

    created = nc.send_event(order_id=order_id, user_id=12, event_type="ORDER_PAID",
                            amount=Decimal("149.90"), currency="USD")

    assert created is True
    assert service.last.url.path == "/internal/notifications"
    assert service.last_json() == {"event_id": f"{order_id}:ORDER_PAID", "user_id": 12,
                                   "order_id": str(order_id), "type": "ORDER_PAID",
                                   "amount": "149.90", "currency": "USD"}


def test_cancelled_event_without_amount(fake):
    service = fake(nc, (201, {}))

    nc.send_event(order_id=uuid.uuid4(), user_id=12, event_type="ORDER_CANCELLED")

    assert (service.last_json()["amount"], service.last_json()["currency"]) == (None, None)


def test_repeated_event(fake):
    fake(nc, (200, {}))

    assert nc.send_event(order_id=uuid.uuid4(), user_id=12, event_type="ORDER_CANCELLED") is False


def test_event_conflict(fake):
    fake(nc, (409, {"detail": {"code": "EVENT_CONFLICT", "message": "x"}}))

    with pytest.raises(nc.EventConflict):
        nc.send_event(order_id=uuid.uuid4(), user_id=12, event_type="ORDER_CANCELLED")


def test_unknown_event_type_is_rejected_before_calling(fake):
    service = fake(nc, (201, {}))

    with pytest.raises(ValueError):
        nc.send_event(order_id=uuid.uuid4(), user_id=12, event_type="ORDER_SHIPPED")
    assert service.requests == []
