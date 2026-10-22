"""POST /orders/{id}/pay and the operation executor, against a real PostgreSQL
(rolled back), with product, inventory, payment and notification services
simulated. Test users: 900501...
"""
import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from app.config import settings
from app.models import Order, PendingOperation
from tests.conftest import bearer

ME, OTHER = 900501, 900502


def placed_order(client, user=ME):
    client.put("/cart/items/1", json={"quantity": 2}, headers=bearer(user))
    version = client.put("/cart/items/2", json={"quantity": 3}, headers=bearer(user)).json()["version"]
    return client.post("/orders", json={"cart_version": version},
                       headers={**bearer(user), "Idempotency-Key": f"buy-{uuid.uuid4()}"}).json()


def pay(client, order_id, simulation="APPROVE", key="pay-1", user=ME):
    return client.post(f"/orders/{order_id}/pay", json={"simulation": simulation},
                       headers={**bearer(user), "Idempotency-Key": key})


def ops(db_session, order_id):
    rows = db_session.scalars(select(PendingOperation)
                              .where(PendingOperation.order_id == uuid.UUID(order_id))
                              .order_by(PendingOperation.id)
                              .execution_options(populate_existing=True)).all()
    db_session.commit()
    return {o.operation_key.split(":", 1)[1]: o for o in rows}


def cart(client, user=ME):
    return client.get("/cart", headers=bearer(user)).json()


# 1, 14, 17. Approved -------------------------------------------------------------------

def test_approved_payment(client, db_session, payments, notifications, inventory):
    order = placed_order(client)

    response = pay(client, order["id"])

    assert response.status_code == 200
    paid = response.json()
    assert (paid["status"], paid["payment_status"], paid["inventory_status"]) == (
        "PAID", "APPROVED", "CONFIRMED")
    # payment-service received the internal key and the amount computed by the backend
    assert payments.requests == [{"key": f"order:{order['id']}", "body": {
        "order_id": order["id"], "user_id": ME, "amount": "1500.28", "currency": "USD",
        "simulation": "APPROVE"}}]
    assert inventory.status[order["id"]] == "CONFIRMED"
    assert notifications.events[f"{order['id']}:ORDER_PAID"]["amount"] == "1500.28"
    assert {k: o.status for k, o in ops(db_session, order["id"]).items()} == {
        "RESOLVE_PAYMENT": "DONE", "CONFIRM_INVENTORY": "DONE", "ORDER_PAID": "DONE"}


def test_cart_is_emptied_and_unlocked_after_approval(client):
    order = placed_order(client)
    before = cart(client)

    pay(client, order["id"])

    after = cart(client)
    assert (after["items"], after["locked"], after["version"]) == ([], False, before["version"] + 1)


def test_approval_and_cart_emptying_happen_in_one_transaction(client, db_session, monkeypatch):
    """If anything fails while applying the approval, neither the order nor
    the cart change: PAID is never visible with the cart still full."""
    from app.services import executor

    order = placed_order(client)

    def broken(*args, **kwargs):
        raise RuntimeError("crash after emptying the cart")
    monkeypatch.setattr(executor, "add_operation", broken)

    with pytest.raises(RuntimeError):
        pay(client, order["id"])

    stored = client.get(f"/orders/{order['id']}", headers=bearer(ME)).json()
    assert stored["status"] == "PAYMENT_PENDING"
    assert len(cart(client)["items"]) == 2  # rolled back together with the order


def test_confirm_then_notify_chain(client, inventory, notifications):
    order = placed_order(client)
    inventory.confirm_mode = "down"

    pay(client, order["id"])

    # No "purchase completed" message before the inventory is confirmed
    assert notifications.events == {}
    inventory.confirm_mode = "ok"
    pay(client, order["id"])
    assert f"{order['id']}:ORDER_PAID" in notifications.events


# 2, 15, 16. Rejected ----------------------------------------------------------------------

def test_rejected_payment_releases_and_notifies(client, db_session, inventory, notifications):
    order = placed_order(client)

    response = pay(client, order["id"], "REJECT")

    assert response.status_code == 200
    rejected = response.json()
    assert (rejected["status"], rejected["payment_status"], rejected["inventory_status"]) == (
        "PAYMENT_REJECTED", "REJECTED", "RELEASED")
    assert inventory.status[order["id"]] == "RELEASED"
    assert notifications.events[f"{order['id']}:PAYMENT_REJECTED"]["type"] == "PAYMENT_REJECTED"
    assert {k: o.status for k, o in ops(db_session, order["id"]).items()} == {
        "RESOLVE_PAYMENT": "DONE", "RELEASE_INVENTORY": "DONE", "PAYMENT_REJECTED": "DONE"}


def test_cart_is_kept_and_unlocked_after_rejection(client):
    order = placed_order(client)
    before = cart(client)

    pay(client, order["id"], "REJECT")

    after = cart(client)
    assert (len(after["items"]), after["locked"], after["version"]) == (2, False, before["version"])
    assert client.put("/cart/items/3", json={"quantity": 1}, headers=bearer(ME)).status_code == 200


# 3-7. SLOW_APPROVE, retries and conflicts ---------------------------------------------------

def test_slow_approve_stays_payment_pending(client, db_session, payments):
    order = placed_order(client)

    response = pay(client, order["id"], "SLOW_APPROVE")

    assert response.status_code == 202
    pending = response.json()
    assert (pending["status"], pending["payment_status"]) == ("PAYMENT_PENDING", "REQUESTED")
    assert payments.payments[order["id"]]["status"] == "APPROVED"  # already approved over there
    op = ops(db_session, order["id"])["RESOLVE_PAYMENT"]
    assert (op.status, op.attempts, op.error_kind) == ("PENDING", 1, "TRANSIENT")
    assert op.next_attempt_at > datetime.now(UTC)
    assert cart(client)["locked"] is True


def test_retry_resolves_the_slow_payment_with_the_same_request(client, payments):
    order = placed_order(client)
    pay(client, order["id"], "SLOW_APPROVE")

    again = pay(client, order["id"], "SLOW_APPROVE")

    assert again.status_code == 200
    assert again.json()["status"] == "PAID"
    assert len(payments.payments) == 1                       # no second payment
    assert payments.requests[0] == payments.requests[1]       # exactly the same request


def test_retry_while_payment_service_is_down(client, db_session, payments):
    order = placed_order(client)
    payments.mode = "down"

    first = pay(client, order["id"])
    again = pay(client, order["id"])

    assert (first.status_code, again.status_code) == (202, 202)
    assert ops(db_session, order["id"])["RESOLVE_PAYMENT"].attempts == 2


@pytest.mark.parametrize("key, simulation", [("pay-2", "APPROVE"), ("pay-1", "REJECT")])
def test_another_key_or_mode_after_starting(client, payments, key, simulation):
    order = placed_order(client)
    payments.mode = "down"
    pay(client, order["id"], "APPROVE", "pay-1")

    response = pay(client, order["id"], simulation, key)

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "IDEMPOTENCY_KEY_REUSED"


@pytest.mark.parametrize("simulation, final", [("APPROVE", "PAID"), ("REJECT", "PAYMENT_REJECTED")])
def test_retry_after_the_final_result(client, payments, simulation, final):
    order = placed_order(client)
    first = pay(client, order["id"], simulation).json()
    requests_before = len(payments.requests)

    again = pay(client, order["id"], simulation)

    assert again.status_code == 200
    assert again.json() == first
    assert len(payments.requests) == requests_before  # no new payment


# 8. Not payable ---------------------------------------------------------------------------

def test_cancelled_order_cannot_be_paid(client, inventory, payments):
    client.put("/cart/items/1", json={"quantity": 1}, headers=bearer(ME))
    inventory.mode = "insufficient"
    cancelled = client.post("/orders", json={"cart_version": 1},
                            headers={**bearer(ME), "Idempotency-Key": "k"}).json()["detail"]["order_id"]

    response = pay(client, cancelled)

    assert response.status_code == 409
    assert response.json()["detail"] == {"code": "ORDER_NOT_PAYABLE", "message": "This order cannot be paid",
                                         "status": "CANCELLED"}
    assert payments.requests == []


def test_expired_order_cannot_be_paid(client, db_session, payments):
    order = placed_order(client)
    db_session.execute(update(Order).where(Order.id == uuid.UUID(order["id"]))
                       .values(expires_at=datetime.now(UTC) - timedelta(seconds=1)))
    db_session.commit()

    response = pay(client, order["id"])

    assert response.json()["detail"]["status"] == "EXPIRED"
    assert client.get(f"/orders/{order['id']}", headers=bearer(ME)).json()["status"] == "PENDING_PAYMENT"
    assert payments.requests == []


def test_reserving_order_cannot_be_paid(client, inventory):
    client.put("/cart/items/1", json={"quantity": 1}, headers=bearer(ME))
    inventory.mode = "down"
    order = client.post("/orders", json={"cart_version": 1},
                        headers={**bearer(ME), "Idempotency-Key": "k"}).json()

    assert pay(client, order["id"]).json()["detail"]["status"] == "RESERVING"


# 12-13. Failures after the approval ---------------------------------------------------------

def test_inventory_down_after_approval_does_not_revert_the_payment(client, db_session, inventory):
    order = placed_order(client)
    inventory.confirm_mode = "down"

    paid = pay(client, order["id"]).json()

    assert (paid["status"], paid["inventory_status"]) == ("PAID", "RESERVED")  # confirmation pending
    op = ops(db_session, order["id"])["CONFIRM_INVENTORY"]
    assert (op.status, op.error_kind) == ("PENDING", "TRANSIENT")


def test_notification_down_does_not_revert_anything(client, db_session, notifications):
    order = placed_order(client)
    notifications.mode = "down"

    paid = pay(client, order["id"]).json()

    assert (paid["status"], paid["inventory_status"]) == ("PAID", "CONFIRMED")
    assert ops(db_session, order["id"])["ORDER_PAID"].status == "PENDING"
    notifications.mode = "ok"
    pay(client, order["id"])  # a later retry delivers it
    assert ops(db_session, order["id"])["ORDER_PAID"].status == "DONE"


# Error classification -----------------------------------------------------------------------

def test_payment_conflict_is_permanent_and_never_a_rejection(client, db_session, payments):
    order = placed_order(client)
    payments.mode = "conflict"

    response = pay(client, order["id"])

    assert response.status_code == 202
    assert response.json()["status"] == "PAYMENT_PENDING"  # not PAYMENT_REJECTED
    op = ops(db_session, order["id"])["RESOLVE_PAYMENT"]
    assert (op.status, op.error_kind) == ("FAILED", "PERMANENT")
    assert "idempotency conflict" in op.last_error
    payments.mode = "ok"
    pay(client, order["id"])  # a permanent failure is not retried automatically
    assert len(payments.requests) == 1


def test_after_ten_failures_the_operation_moves_to_the_slow_lane(client, db_session, payments):
    order = placed_order(client)
    payments.mode = "down"
    pay(client, order["id"])
    db_session.execute(update(PendingOperation)
                       .where(PendingOperation.order_id == uuid.UUID(order["id"]))
                       .values(attempts=settings.worker_max_fast_attempts - 1))
    db_session.commit()

    pay(client, order["id"])

    op = ops(db_session, order["id"])["RESOLVE_PAYMENT"]
    assert (op.status, op.error_kind, op.attempts) == ("FAILED", "TRANSIENT", 10)
    wait = (op.next_attempt_at - datetime.now(UTC)).total_seconds()
    assert settings.worker_slow_retry_seconds - 5 < wait <= settings.worker_slow_retry_seconds


# 18. No secrets ------------------------------------------------------------------------------

def test_payloads_hold_business_data_only(client, db_session, inventory):
    order = placed_order(client)
    pay(client, order["id"], "REJECT")
    second = placed_order(client)
    pay(client, second["id"])

    for order_id in (order["id"], second["id"]):
        for op in ops(db_session, order_id).values():
            text = json.dumps(op.payload)
            assert set(op.payload) <= {"user_id", "amount", "currency", "simulation", "event_type"}
            assert "test-internal-key" not in text and "Bearer" not in text and "eyJ" not in text


# 19. Authorization --------------------------------------------------------------------------

def test_only_the_owner_can_pay(client, payments):
    order = placed_order(client, ME)

    assert pay(client, order["id"], user=OTHER).status_code == 404
    assert client.post(f"/orders/{order['id']}/pay", json={"simulation": "APPROVE"},
                       headers={**bearer(1, "admin"), "Idempotency-Key": "k"}).status_code == 404
    assert client.post(f"/orders/{order['id']}/pay", json={}, headers={"Idempotency-Key": "k"}).status_code == 401
    assert pay(client, uuid.uuid4()).status_code == 404
    assert payments.requests == []


def test_payment_body_rejects_card_data(client):
    order = placed_order(client)

    response = client.post(f"/orders/{order['id']}/pay",
                           json={"simulation": "APPROVE", "card_number": "4111111111111111"},
                           headers={**bearer(ME), "Idempotency-Key": "k"})

    assert response.status_code == 422


def test_a_repeated_payment_result_is_not_applied_twice(client, db_session):
    """If RESOLVE_PAYMENT ran again after the order was PAID (for example an
    operation reactivated by an admin in stage 6), the conditional UPDATE
    finds the order no longer PAYMENT_PENDING and changes nothing: the new
    cart of the customer is not emptied again."""
    from app.services import executor

    order = placed_order(client)
    pay(client, order["id"])
    client.put("/cart/items/5", json={"quantity": 1}, headers=bearer(ME))  # a new cart
    new_cart = cart(client)
    op = ops(db_session, order["id"])["RESOLVE_PAYMENT"]
    db_session.execute(update(PendingOperation).where(PendingOperation.id == op.id)
                       .values(status="PENDING", completed_at=None))
    db_session.commit()

    assert executor.run_operation(db_session, op.id, ignore_schedule=True) == "DONE"

    assert cart(client) == new_cart
    assert len([o for o in ops(db_session, order["id"]) if o == "CONFIRM_INVENTORY"]) == 1
