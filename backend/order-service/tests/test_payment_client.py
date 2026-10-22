import uuid
from decimal import Decimal

import pytest

from app.clients import payment_client as pc
from app.clients.base import ServiceUnavailable


def payment(order_id, status="APPROVED", simulation="APPROVE"):
    # Same shape as PaymentOut in payment-service
    return {"id": str(uuid.uuid4()), "order_id": str(order_id), "user_id": 12,
            "amount": "149.90", "currency": "USD", "status": status, "simulation": simulation,
            "failure_reason": None, "processed_at": "2026-10-21T10:00:00Z",
            "created_at": "2026-10-21T10:00:00Z"}


def pay(order_id, simulation="APPROVE"):
    return pc.create_payment(order_id=order_id, user_id=12, amount=Decimal("149.90"),
                             currency="USD", simulation=simulation)


def test_internal_key_is_built_from_the_order():
    order_id = uuid.uuid4()

    assert pc.payment_key(order_id) == f"order:{order_id}"
    assert len(pc.payment_key(order_id)) <= 100  # limit of the header in payment-service


def test_create_sends_the_agreed_request(fake):
    order_id = uuid.uuid4()
    service = fake(pc, (201, payment(order_id)))

    result = pay(order_id, "SLOW_APPROVE")

    assert service.last.url.path == "/internal/payments"
    assert service.last.headers["Idempotency-Key"] == f"order:{order_id}"
    assert service.last_json() == {"order_id": str(order_id), "user_id": 12, "amount": "149.90",
                                   "currency": "USD", "simulation": "SLOW_APPROVE"}
    assert (result.status, result.amount, result.created) == ("APPROVED", Decimal("149.90"), True)


def test_repeated_payment_returns_the_original(fake):
    order_id = uuid.uuid4()
    fake(pc, (200, payment(order_id, "REJECTED", "REJECT")))

    result = pay(order_id)

    assert (result.status, result.simulation, result.created) == ("REJECTED", "REJECT", False)


@pytest.mark.parametrize("code, error", [
    ("IDEMPOTENCY_KEY_REUSED", pc.IdempotencyKeyReused),
    ("ORDER_ALREADY_HAS_PAYMENT", pc.OrderAlreadyHasPayment),
])
def test_payment_conflicts(fake, code, error):
    fake(pc, (409, {"detail": {"code": code, "message": "x"}}))

    with pytest.raises(error):
        pay(uuid.uuid4())


def test_simulation_disabled_is_transient(fake):
    # 503 "No payment provider configured": nothing was created, it can be retried later
    fake(pc, (503, {"detail": "No payment provider configured"}))

    with pytest.raises(ServiceUnavailable):
        pay(uuid.uuid4())


def test_get_payment_by_order(fake):
    order_id = uuid.uuid4()
    service = fake(pc, (200, payment(order_id)))

    assert pc.get_payment_by_order(order_id).status == "APPROVED"
    assert service.last.url.params["order_id"] == str(order_id)


def test_get_payment_that_was_never_created(fake):
    fake(pc, (404, {"detail": "No payment for this order"}))

    assert pc.get_payment_by_order(uuid.uuid4()) is None
