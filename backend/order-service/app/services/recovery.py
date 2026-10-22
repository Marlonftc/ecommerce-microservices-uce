"""Order changes made by time or by the customer: expiration, the RESERVING
sweep and the manual cancellation.

Every change is a conditional UPDATE on the order row, and the follow-up
operations are created only for the orders that the UPDATE really changed,
in the same transaction. Starting a payment uses the opposite condition on
the same row (expires_at > now()), so payment and expiration never both win.
"""
import uuid
from datetime import timedelta

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Order
from app.services import executor
from app.services.operations import add_operation
from app.services.order_service import OrderNotFound


class OrderNotCancellable(Exception):
    def __init__(self, status: str):
        super().__init__(f"order is {status}")
        self.status = status


def _cancelled_notification(order_id, user_id, total, currency) -> dict:
    return {"event_type": "ORDER_CANCELLED", "user_id": user_id,
            "amount": str(total), "currency": currency}


def expire_orders(db: Session, limit: int = 50) -> int:
    """PENDING_PAYMENT orders whose time ran out. PAYMENT_PENDING never expires."""
    due = (select(Order.id)
           .where(Order.status == "PENDING_PAYMENT", Order.expires_at <= func.now())
           .order_by(Order.expires_at).limit(limit)
           .with_for_update(skip_locked=True))
    try:
        rows = db.execute(update(Order)
                          .where(Order.id.in_(due.scalar_subquery()),
                                 Order.status == "PENDING_PAYMENT", Order.expires_at <= func.now())
                          .values(status="CANCELLED", cancel_reason="EXPIRED")
                          .returning(Order.id, Order.user_id, Order.total, Order.currency)).all()
        for r in rows:
            add_operation(db, r.id, "RELEASE_INVENTORY")
            add_operation(db, r.id, "SEND_NOTIFICATION", "ORDER_CANCELLED",
                          _cancelled_notification(r.id, r.user_id, r.total, r.currency))
        db.commit()
    except Exception:
        db.rollback()
        raise
    return len(rows)


def sweep_reserving(db: Session, limit: int = 50) -> int:
    """RESERVING for too long: inventory never answered. Cancelled with
    RESERVATION_TIMEOUT and a release (a late reservation will be freed, or
    rejected by the tombstone). No notification: the order was never confirmed.
    inventory_status is not touched: RELEASED is written when inventory confirms it."""
    limit_time = func.now() - timedelta(minutes=settings.reserving_sweep_minutes)
    old = (select(Order.id)
           .where(Order.status == "RESERVING", Order.created_at <= limit_time)
           .order_by(Order.created_at).limit(limit)
           .with_for_update(skip_locked=True))
    try:
        rows = db.execute(update(Order)
                          .where(Order.id.in_(old.scalar_subquery()), Order.status == "RESERVING")
                          .values(status="CANCELLED", cancel_reason="RESERVATION_TIMEOUT")
                          .returning(Order.id)).all()
        for r in rows:
            add_operation(db, r.id, "RELEASE_INVENTORY")
        db.commit()
    except Exception:
        db.rollback()
        raise
    return len(rows)


def cancel_by_user(db: Session, user_id: int, order_id: uuid.UUID) -> Order:
    """Owner only. From PENDING_PAYMENT; repeating it after it worked returns the order."""
    try:
        row = db.execute(update(Order)
                         .where(Order.id == order_id, Order.user_id == user_id,
                                Order.status == "PENDING_PAYMENT")
                         .values(status="CANCELLED", cancel_reason="USER")
                         .returning(Order.total, Order.currency)).one_or_none()
        if row:
            add_operation(db, order_id, "RELEASE_INVENTORY")
            add_operation(db, order_id, "SEND_NOTIFICATION", "ORDER_CANCELLED",
                          _cancelled_notification(order_id, user_id, row.total, row.currency))
        db.commit()
    except Exception:
        db.rollback()
        raise

    order = db.scalar(select(Order).where(Order.id == order_id, Order.user_id == user_id)
                      .execution_options(populate_existing=True))
    db.commit()
    if order is None:
        raise OrderNotFound
    if not row and (order.status, order.cancel_reason) != ("CANCELLED", "USER"):
        raise OrderNotCancellable(order.status)
    # The operations are deterministic (same keys): a repeated cancellation adds none,
    # and only retries what is still pending
    executor.run_order_operations(db, order_id, ignore_schedule=True)
    return db.get(Order, order_id)
