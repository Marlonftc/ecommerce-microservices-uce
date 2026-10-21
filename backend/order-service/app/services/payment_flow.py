"""POST /orders/{id}/pay.

The payment intent is saved first, in one local transaction: the order moves
to PAYMENT_PENDING with the customer's key, the mode and payment_status
REQUESTED, together with the RESOLVE_PAYMENT operation that holds everything
needed to repeat the exact request. Only then is payment-service called,
through the shared executor.
"""
import uuid

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.models import Order
from app.services import executor
from app.services.operations import add_operation
from app.services.order_service import OrderNotFound


class IdempotencyKeyReused(Exception):
    """The payment of this order was started with another key or mode."""


class OrderNotPayable(Exception):
    def __init__(self, status: str):
        super().__init__(f"order is {status}")
        self.status = status


def _owned(db: Session, order_id: uuid.UUID, user_id: int) -> Order:
    order = db.scalar(select(Order).where(Order.id == order_id, Order.user_id == user_id)
                      .execution_options(populate_existing=True))
    db.commit()
    if order is None:
        raise OrderNotFound  # also when it belongs to another user
    return order


def _already_started(db: Session, order: Order, key: str, simulation: str) -> Order:
    if (order.payment_key, order.payment_simulation) != (key, simulation):
        raise IdempotencyKeyReused
    # PAYMENT_PENDING: the same request again while the result is unknown, so
    # try to resolve it now; payment-service receives exactly the same request
    # (same internal key) and never creates a second payment.
    # PAID or PAYMENT_REJECTED: the original result is returned. No new payment
    # is possible (RESOLVE_PAYMENT is DONE); pending follow-ups are retried.
    executor.run_order_operations(db, order.id, ignore_schedule=True)
    return _owned(db, order.id, order.user_id)


def pay(db: Session, user_id: int, order_id: uuid.UUID, key: str, simulation: str) -> Order:
    order = _owned(db, order_id, user_id)
    if order.payment_key is not None:
        return _already_started(db, order, key, simulation)

    try:
        started = db.execute(
            update(Order)
            .where(Order.id == order_id, Order.user_id == user_id,
                   Order.status == "PENDING_PAYMENT", Order.expires_at > func.now())
            .values(status="PAYMENT_PENDING", payment_key=key, payment_simulation=simulation,
                    payment_status="REQUESTED")
            .returning(Order.user_id, Order.total, Order.currency)
        ).one_or_none()
        if started:
            # Same transaction: the intent and the operation exist together or not at all.
            # No secrets: the internal key "order:<id>" is rebuilt when calling.
            add_operation(db, order_id, "RESOLVE_PAYMENT", payload={
                "user_id": started.user_id, "amount": str(started.total),
                "currency": started.currency, "simulation": simulation})
        db.commit()
    except Exception:
        db.rollback()
        raise

    if not started:
        # The conditional UPDATE did not apply: find out why before answering
        order = _owned(db, order_id, user_id)
        if order.payment_key is not None:
            return _already_started(db, order, key, simulation)  # a simultaneous request won
        raise OrderNotPayable(order.status if order.status != "PENDING_PAYMENT" else "EXPIRED")

    executor.run_order_operations(db, order_id)
    return _owned(db, order_id, user_id)
