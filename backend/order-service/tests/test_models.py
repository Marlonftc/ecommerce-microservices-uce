"""Constraints of the order-service tables, checked against PostgreSQL.

Test users have ids far from real ones (900001...), so real data in the
database never changes the result. Every test is rolled back.
"""
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models import Cart, CartItem, Order, OrderItem, PendingOperation

U1, U2 = 900001, 900002
SOON = datetime.now(UTC) + timedelta(minutes=15)


def order(user_id=U1, status="RESERVING", **changes) -> Order:
    values = dict(user_id=user_id, status=status, total=Decimal("149.90"),
                  checkout_key=f"checkout-{uuid.uuid4()}", cart_version=3)
    if status == "PENDING_PAYMENT":
        values.update(expires_at=SOON, inventory_status="RESERVED")
    if status in ("PAYMENT_PENDING", "PAID", "PAYMENT_REJECTED"):
        values.update(payment_key=f"pay-{uuid.uuid4()}", payment_simulation="APPROVE",
                      payment_status={"PAYMENT_PENDING": "REQUESTED", "PAID": "APPROVED",
                                      "PAYMENT_REJECTED": "REJECTED"}[status])
    if status == "CANCELLED":
        values.update(cancel_reason="USER")
    values.update(changes)
    return Order(**values)


def add(db, *objects):
    db.add_all(objects)
    db.flush()
    return objects[0] if len(objects) == 1 else objects


def assert_rejected(db, *objects):
    db.add_all(objects)
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


# --- carts and cart_items ------------------------------------------------------

def test_new_cart_starts_at_version_zero(db_session):
    cart = add(db_session, Cart(user_id=U1))
    db_session.refresh(cart)

    assert cart.version == 0


def test_cart_version_cannot_be_negative(db_session):
    assert_rejected(db_session, Cart(user_id=U1, version=-1))


def test_cart_user_must_be_positive(db_session):
    assert_rejected(db_session, Cart(user_id=0))


@pytest.mark.parametrize("quantity", [1, 99])
def test_cart_quantity_limits_are_accepted(db_session, quantity):
    add(db_session, Cart(user_id=U1))
    add(db_session, CartItem(user_id=U1, product_id=1, quantity=quantity))


@pytest.mark.parametrize("quantity", [0, -1, 100])
def test_invalid_cart_quantities_are_rejected(db_session, quantity):
    add(db_session, Cart(user_id=U1))
    assert_rejected(db_session, CartItem(user_id=U1, product_id=1, quantity=quantity))


def test_a_product_appears_once_per_cart(db_session):
    add(db_session, Cart(user_id=U1))
    add(db_session, CartItem(user_id=U1, product_id=1, quantity=1))

    assert_rejected(db_session, CartItem(user_id=U1, product_id=1, quantity=2))


def test_cart_item_needs_its_cart(db_session):
    assert_rejected(db_session, CartItem(user_id=U2, product_id=1, quantity=1))


# --- orders ----------------------------------------------------------------------

@pytest.mark.parametrize("status", ["RESERVING", "PENDING_PAYMENT", "PAYMENT_PENDING",
                                    "PAID", "PAYMENT_REJECTED", "CANCELLED"])
def test_the_six_states_are_accepted(db_session, status):
    add(db_session, order(status=status))


def test_unknown_state_is_rejected(db_session):
    assert_rejected(db_session, order(status="SHIPPED"))


def test_defaults_and_dates_with_time_zone(db_session):
    o = add(db_session, order())
    db_session.refresh(o)

    assert (o.currency, o.inventory_status, o.payment_status) == ("USD", "PENDING", None)
    assert o.created_at.utcoffset() is not None


@pytest.mark.parametrize("changes", [
    {"currency": "EUR"},
    {"total": Decimal("0")},
    {"total": Decimal("-1.00")},
    {"user_id": 0},
    {"cart_version": -1},
    {"inventory_status": "LOST"},
])
def test_invalid_order_values_are_rejected(db_session, changes):
    assert_rejected(db_session, order(**changes))


@pytest.mark.parametrize("second", ["RESERVING", "PENDING_PAYMENT", "PAYMENT_PENDING"])
def test_two_active_orders_of_the_same_user_are_rejected(db_session, second):
    add(db_session, order(U1, "PENDING_PAYMENT"))

    assert_rejected(db_session, order(U1, second))


def test_active_orders_of_different_users_at_the_same_time(db_session):
    add(db_session, order(U1, "PENDING_PAYMENT"), order(U2, "PENDING_PAYMENT"))


@pytest.mark.parametrize("terminal", ["PAID", "PAYMENT_REJECTED", "CANCELLED"])
def test_new_order_after_the_previous_one_ended(db_session, terminal):
    add(db_session, order(U1, terminal), order(U1, terminal))  # several finished orders are fine

    add(db_session, order(U1, "RESERVING"))


def test_an_order_can_finish_and_free_the_user(db_session):
    first = add(db_session, order(U1, "PENDING_PAYMENT"))
    first.status, first.cancel_reason = "CANCELLED", "EXPIRED"
    db_session.flush()

    add(db_session, order(U1, "RESERVING"))


def test_checkout_key_is_unique_per_user(db_session):
    add(db_session, order(U1, "PAID", checkout_key="same-key"))

    assert_rejected(db_session, order(U1, "CANCELLED", checkout_key="same-key"))


def test_two_users_can_use_the_same_checkout_key(db_session):
    add(db_session, order(U1, checkout_key="same-key"), order(U2, checkout_key="same-key"))


def test_pending_payment_needs_a_deadline(db_session):
    assert_rejected(db_session, order(status="PENDING_PAYMENT", expires_at=None))


def test_cancelled_order_needs_a_valid_reason(db_session):
    assert_rejected(db_session, order(status="CANCELLED", cancel_reason=None))
    assert_rejected(db_session, order(status="CANCELLED", cancel_reason="BORED"))


def test_only_cancelled_orders_have_a_reason(db_session):
    assert_rejected(db_session, order(status="RESERVING", cancel_reason="USER"))


@pytest.mark.parametrize("changes", [
    {"payment_key": "k"},                                      # key without mode and status
    {"payment_key": "k", "payment_simulation": "APPROVE"},     # without status
    {"payment_status": "REQUESTED"},                           # status without key
])
def test_payment_intent_is_recorded_complete(db_session, changes):
    assert_rejected(db_session, order(**changes))


@pytest.mark.parametrize("status", ["PAYMENT_PENDING", "PAID", "PAYMENT_REJECTED"])
def test_payment_states_need_the_payment_intent(db_session, status):
    assert_rejected(db_session, order(status=status, payment_key=None,
                                      payment_simulation=None, payment_status=None))


def test_payment_values_are_restricted(db_session):
    assert_rejected(db_session, order(status="PAYMENT_PENDING", payment_simulation="MAYBE"))
    assert_rejected(db_session, order(status="PAYMENT_PENDING", payment_status="LOST"))


def test_final_payment_states_match_the_payment(db_session):
    assert_rejected(db_session, order(status="PAID", payment_status="REJECTED"))
    assert_rejected(db_session, order(status="PAID", payment_status="REQUESTED"))
    assert_rejected(db_session, order(status="PAYMENT_REJECTED", payment_status="APPROVED"))


def test_legitimate_intermediate_states_are_allowed(db_session):
    # Payment requested but the answer never arrived: no payment_id yet
    add(db_session, order(U1, "PAYMENT_PENDING", payment_id=None))
    # Paid, but the confirmation in inventory is still pending
    add(db_session, order(U2, "PAID", inventory_status="RESERVED"))


# --- order_items -----------------------------------------------------------------

def item(order_id, product_id=1, unit_price="74.95", quantity=2, subtotal="149.90") -> OrderItem:
    return OrderItem(order_id=order_id, product_id=product_id, sku="LAP-001", name="Laptop",
                     unit_price=Decimal(unit_price), quantity=quantity, subtotal=Decimal(subtotal))


def test_order_item_snapshot(db_session):
    o = add(db_session, order())
    add(db_session, item(o.id), item(o.id, product_id=2, unit_price="0.10", quantity=99, subtotal="9.90"))

    stored = db_session.scalars(select(OrderItem).where(OrderItem.order_id == o.id)
                                .order_by(OrderItem.product_id)).all()
    assert [(i.unit_price, i.quantity, i.subtotal) for i in stored] == [
        (Decimal("74.95"), 2, Decimal("149.90")), (Decimal("0.10"), 99, Decimal("9.90"))]


@pytest.mark.parametrize("changes", [
    {"subtotal": "149.91"},                          # does not match price * quantity
    {"unit_price": "0", "subtotal": "0"},
    {"unit_price": "-1.00", "subtotal": "-2.00"},
    {"quantity": 0, "subtotal": "0"},
    {"quantity": 100, "subtotal": "7495.00"},
    {"product_id": 0},
])
def test_invalid_order_items_are_rejected(db_session, changes):
    o = add(db_session, order())
    values = dict(unit_price="74.95", quantity=2, subtotal="149.90", product_id=1)
    values.update(changes)

    assert_rejected(db_session, item(o.id, **values))


def test_a_product_appears_once_per_order(db_session):
    o = add(db_session, order())
    add(db_session, item(o.id))

    assert_rejected(db_session, item(o.id))


def test_order_item_needs_its_order(db_session):
    assert_rejected(db_session, item(uuid.uuid4()))


# --- pending_operations ----------------------------------------------------------

def operation(order_id, kind="CONFIRM_INVENTORY", **changes) -> PendingOperation:
    values = dict(order_id=order_id, kind=kind, operation_key=f"{order_id}:{kind}")
    values.update(changes)
    return PendingOperation(**values)


def test_new_operation_defaults(db_session):
    o = add(db_session, order())
    op = add(db_session, operation(o.id, payload={"amount": "149.90", "currency": "USD"}))
    db_session.refresh(op)

    assert (op.status, op.attempts, op.locked_until, op.lease_token) == ("PENDING", 0, None, None)
    assert op.payload == {"amount": "149.90", "currency": "USD"}
    assert op.next_attempt_at is not None


@pytest.mark.parametrize("kind", ["RESOLVE_PAYMENT", "CONFIRM_INVENTORY",
                                  "RELEASE_INVENTORY", "SEND_NOTIFICATION"])
def test_the_four_kinds_are_accepted(db_session, kind):
    o = add(db_session, order())
    add(db_session, operation(o.id, kind))


def test_notification_key_is_the_event_id(db_session):
    o = add(db_session, order())
    add(db_session, operation(o.id, "SEND_NOTIFICATION", operation_key=f"{o.id}:ORDER_PAID"))
    add(db_session, operation(o.id, "SEND_NOTIFICATION", operation_key=f"{o.id}:PAYMENT_REJECTED"))


def test_operation_key_is_unique(db_session):
    o = add(db_session, order())
    add(db_session, operation(o.id))

    assert_rejected(db_session, operation(o.id))


@pytest.mark.parametrize("changes", [
    {"kind": "SEND_EMAIL"},
    {"status": "RUNNING"},
    {"attempts": -1},
    {"error_kind": "MAYBE"},
    {"status": "DONE"},                                            # done without completed_at
    {"completed_at": datetime.now(UTC)},                            # completed but not done
    {"locked_until": datetime.now(UTC)},                            # lease without token
    {"lease_token": uuid.uuid4()},                                  # token without deadline
])
def test_invalid_operations_are_rejected(db_session, changes):
    o = add(db_session, order())

    assert_rejected(db_session, operation(o.id, **changes))


def test_valid_lease_and_completion(db_session):
    o = add(db_session, order())
    leased = add(db_session, operation(o.id, "RESOLVE_PAYMENT", locked_until=SOON, lease_token=uuid.uuid4()))
    add(db_session, operation(o.id, "RELEASE_INVENTORY", status="DONE", completed_at=datetime.now(UTC)))
    add(db_session, operation(o.id, "CONFIRM_INVENTORY", status="FAILED", attempts=10,
                              error_kind="TRANSIENT", last_error="inventory-service unavailable"))

    assert leased.lease_token is not None


def test_operation_needs_its_order(db_session):
    assert_rejected(db_session, operation(uuid.uuid4()))
