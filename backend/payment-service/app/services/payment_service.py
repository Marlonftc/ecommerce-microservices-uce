"""Simulated payments. Every payment goes PENDING -> APPROVED or REJECTED.

The work is split into two short transactions:
  A) find or create the payment as PENDING (committed)
  B) resolve it with the simulation mode that was stored in A (committed)
If the service stops between A and B, the payment stays PENDING. Repeating
the request with the same key finds it in A and completes it in B.
"""
import uuid
from datetime import UTC, datetime

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Payment
from app.schemas import PaymentIn

RESULT_BY_SIMULATION = {
    "APPROVE": ("APPROVED", None),
    "SLOW_APPROVE": ("APPROVED", None),
    "REJECT": ("REJECTED", "simulated rejection"),
}


class IdempotencyKeyReused(Exception):
    """The same key was used before with different payment data."""


class OrderAlreadyHasPayment(Exception):
    """The order already has a payment created with another key."""


class PaymentNotFound(Exception):
    pass


def _same_request(payment: Payment, data: PaymentIn) -> bool:
    return (payment.order_id, payment.user_id, payment.amount, payment.currency, payment.simulation) == (
        data.order_id, data.user_id, data.amount, data.currency, data.simulation
    )


def _find_or_create(db: Session, data: PaymentIn, idempotency_key: str) -> tuple[Payment, bool]:
    """Transaction A. Returns (payment, True if it was created now)."""
    for _ in range(2):  # second round only if a simultaneous request inserted first
        existing = db.scalar(
            select(Payment)
            .where(or_(Payment.idempotency_key == idempotency_key, Payment.order_id == data.order_id))
            .with_for_update()  # waits if another request is working on this payment
        )
        if existing is not None:
            db.commit()
            if existing.idempotency_key != idempotency_key:
                raise OrderAlreadyHasPayment
            if not _same_request(existing, data):
                raise IdempotencyKeyReused
            return existing, False

        payment = Payment(idempotency_key=idempotency_key, status="PENDING", **data.model_dump())
        db.add(payment)
        try:
            db.commit()
            return payment, True
        except IntegrityError:
            # UNIQUE on order_id or idempotency_key: another request was first
            db.rollback()
    raise RuntimeError("payment could not be created")


def _resolve(db: Session, payment_id: uuid.UUID) -> Payment:
    """Transaction B. Only a PENDING payment can change: APPROVED and REJECTED are final."""
    payment = db.get(Payment, payment_id)
    status, reason = RESULT_BY_SIMULATION[payment.simulation]  # the STORED mode, not the new request
    db.execute(
        update(Payment)
        .where(Payment.id == payment_id, Payment.status == "PENDING")
        .values(status=status, failure_reason=reason, processed_at=datetime.now(UTC))
    )
    db.commit()
    db.refresh(payment)
    return payment


def process_payment(db: Session, data: PaymentIn, idempotency_key: str) -> tuple[Payment, bool]:
    """Returns (payment, True if it was created by this request)."""
    payment, created = _find_or_create(db, data, idempotency_key)
    if payment.status == "PENDING":
        payment = _resolve(db, payment.id)
    return payment, created


def get_by_order(db: Session, order_id: uuid.UUID) -> Payment:
    payment = db.scalar(select(Payment).where(Payment.order_id == order_id))
    if payment is None:
        raise PaymentNotFound
    return payment


def get_for_user(db: Session, payment_id: uuid.UUID, user_id: int, is_admin: bool) -> Payment:
    query = select(Payment).where(Payment.id == payment_id)
    if not is_admin:
        # The owner filter is part of the query: another user's payment is never loaded
        query = query.where(Payment.user_id == user_id)
    payment = db.scalar(query)
    if payment is None:
        raise PaymentNotFound
    return payment


def list_for_user(db: Session, user_id: int, is_admin: bool, limit: int, offset: int) -> list[Payment]:
    query = select(Payment)
    if not is_admin:
        query = query.where(Payment.user_id == user_id)
    return list(db.scalars(query.order_by(Payment.created_at.desc()).limit(limit).offset(offset)))
