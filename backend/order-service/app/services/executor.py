"""Runs pending operations. Shared by the HTTP requests and, in stage 6, the worker.

Each run has three steps, and no SQL transaction is open during the HTTP call:

  1. claim   UPDATE ... SET lease_token, locked_until WHERE the operation is
             free (or its lease expired). Commit.
  2. call    the other service. Every call is idempotent: repeating it is safe.
  3. finish  in one transaction: lock the operation only if lease_token is
             still ours, apply the result to the order with conditional
             UPDATEs, add follow-up operations and mark it DONE.

A runner whose lease expired finds a different lease_token in step 3 and
writes nothing, so it can never overwrite the work of the runner that
claimed the operation after it.
"""
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, or_, select, true, update
from sqlalchemy.orm import Session

from app.clients import inventory_client, notification_client, payment_client
from app.clients.base import ServiceUnavailable, UnexpectedResponse
from app.config import settings
from app.models import Order, PendingOperation
from app.services import cart_service
from app.services.operations import add_operation

logger = logging.getLogger(__name__)

DONE, RETRY, FAILED, SKIPPED, LOST = "DONE", "RETRY", "FAILED", "SKIPPED", "LOST"


class Transient(Exception):
    """No definitive answer: try again later."""


class Permanent(Exception):
    """Retrying will not help; it needs to be reviewed (stage 6: admin)."""


@dataclass(frozen=True)
class Claim:
    id: int
    order_id: uuid.UUID
    kind: str
    payload: dict[str, Any]
    token: uuid.UUID


# --- 1. claim ------------------------------------------------------------------------

# PENDING, or FAILED/TRANSIENT (slow lane). FAILED/PERMANENT and DONE never run on their own.
ELIGIBLE = or_(PendingOperation.status == "PENDING",
               and_(PendingOperation.status == "FAILED", PendingOperation.error_kind == "TRANSIENT"))


def _lease_free(now: datetime):
    return or_(PendingOperation.locked_until.is_(None), PendingOperation.locked_until < now)


def _take(db: Session, *conditions) -> Claim | None:
    """One UPDATE that checks eligibility and writes the lease, then commit."""
    now = datetime.now(UTC)
    token = uuid.uuid4()
    row = db.execute(
        update(PendingOperation).where(*conditions)
        .values(lease_token=token, locked_until=now + timedelta(seconds=settings.worker_lease_seconds),
                attempts=PendingOperation.attempts + 1)
        .returning(PendingOperation.id, PendingOperation.order_id, PendingOperation.kind,
                   PendingOperation.payload)
    ).one_or_none()
    db.commit()  # the lease is visible to everyone before any HTTP call
    if row is None:
        return None
    return Claim(row.id, row.order_id, row.kind, row.payload, token)


def claim(db: Session, op_id: int, ignore_schedule: bool = False) -> Claim | None:
    """Takes one known operation if it is eligible and nobody holds a valid lease.

    ignore_schedule: a customer retry (same Idempotency-Key) or an admin
    reactivation runs it now even if its next attempt is scheduled later.
    """
    now = datetime.now(UTC)
    conditions = [PendingOperation.id == op_id, ELIGIBLE, _lease_free(now)]
    if not ignore_schedule:
        conditions.append(PendingOperation.next_attempt_at <= now)
    return _take(db, *conditions)


def claim_next(db: Session) -> Claim | None:
    """Worker: chooses AND claims the next due operation in one statement.

    SKIP LOCKED makes two workers pick different rows instead of waiting for
    each other. Claiming one at a time starts each lease right before its own
    call, so a lease never expires while the operation waits in a batch.
    """
    now = datetime.now(UTC)
    next_due = (select(PendingOperation.id)
                .where(ELIGIBLE, _lease_free(now), PendingOperation.next_attempt_at <= now)
                .order_by(PendingOperation.next_attempt_at, PendingOperation.id)
                .limit(1)
                .with_for_update(skip_locked=True)
                .scalar_subquery())
    return _take(db, PendingOperation.id == next_due)


# --- 3. finish ---------------------------------------------------------------------------

def _lock_if_still_ours(db: Session, c: Claim) -> PendingOperation | None:
    return db.scalar(select(PendingOperation)
                     .where(PendingOperation.id == c.id, PendingOperation.lease_token == c.token,
                            PendingOperation.status != "DONE")
                     .with_for_update())


def _finish(db: Session, c: Claim, apply: Callable[[Session], None]) -> str:
    try:
        op = _lock_if_still_ours(db, c)
        if op is None:
            db.rollback()
            logger.warning("operation %s: lease lost, result not applied", c.id)
            return LOST
        apply(db)
        op.status, op.completed_at = "DONE", datetime.now(UTC)
        op.lease_token = op.locked_until = None
        op.last_error = op.error_kind = None
        db.commit()
        return DONE
    except Exception:
        db.rollback()
        raise


def _backoff(attempts: int) -> float:
    return min(settings.worker_poll_seconds * 2 ** attempts, settings.worker_backoff_max_seconds)


def _fail(db: Session, c: Claim, error: Exception, permanent: bool) -> str:
    """Records the error. Only the holder of the lease can do it."""
    op = _lock_if_still_ours(db, c)
    if op is None:
        db.rollback()
        return LOST
    now = datetime.now(UTC)
    op.last_error = str(error)[:500]
    op.lease_token = op.locked_until = None
    if permanent:
        op.status, op.error_kind = "FAILED", "PERMANENT"
    elif op.attempts >= settings.worker_max_fast_attempts:
        # Slow lane: the worker retries it every WORKER_SLOW_RETRY_SECONDS, never drops it
        op.status, op.error_kind = "FAILED", "TRANSIENT"
        op.next_attempt_at = now + timedelta(seconds=settings.worker_slow_retry_seconds)
    else:
        op.error_kind = "TRANSIENT"
        op.next_attempt_at = now + timedelta(seconds=_backoff(op.attempts))
    db.commit()
    log = logger.error if permanent else logger.warning
    log("operation %s (%s) %s: %s", c.id, c.kind, "failed" if permanent else "will retry", error)
    return FAILED if op.status == "FAILED" else RETRY


# --- 2. call + what each kind applies -------------------------------------------------------

def _notification_payload(order: Order, event_type: str) -> dict[str, Any]:
    # Business data only: never tokens or keys
    return {"event_type": event_type, "user_id": order.user_id,
            "amount": str(order.total), "currency": order.currency}


def _resolve_payment(db: Session, c: Claim) -> str:
    p = c.payload
    try:
        payment = payment_client.create_payment(
            order_id=c.order_id, user_id=p["user_id"], amount=Decimal(p["amount"]),
            currency=p["currency"], simulation=p["simulation"])
    except ServiceUnavailable as exc:
        raise Transient(exc)
    except (payment_client.IdempotencyKeyReused, payment_client.OrderAlreadyHasPayment) as exc:
        # The same deterministic key must always match: an inconsistency to review
        raise Permanent(f"payment-service idempotency conflict: {type(exc).__name__}")
    except UnexpectedResponse as exc:
        raise Permanent(exc)

    if payment.status == "PENDING":
        raise Transient("payment still PENDING in payment-service")

    def apply(db: Session) -> None:
        order = db.get(Order, c.order_id)
        if payment.status == "APPROVED":
            # Lock order: cart first, then the order (the same order as the checkout)
            cart_service.lock_cart(db, order.user_id)
            paid = db.execute(update(Order)
                              .where(Order.id == c.order_id, Order.status == "PAYMENT_PENDING")
                              .values(status="PAID", payment_status="APPROVED", payment_id=payment.id)
                              .returning(Order.id)).one_or_none()
            if paid:
                cart_service.empty_locked_cart(db, order.user_id)
                add_operation(db, c.order_id, "CONFIRM_INVENTORY")
        else:  # REJECTED: an explicit answer, never a timeout
            rejected = db.execute(update(Order)
                                  .where(Order.id == c.order_id, Order.status == "PAYMENT_PENDING")
                                  .values(status="PAYMENT_REJECTED", payment_status="REJECTED",
                                          payment_id=payment.id)
                                  .returning(Order.id)).one_or_none()
            if rejected:
                add_operation(db, c.order_id, "RELEASE_INVENTORY")
                db.refresh(order)
                add_operation(db, c.order_id, "SEND_NOTIFICATION", "PAYMENT_REJECTED",
                              _notification_payload(order, "PAYMENT_REJECTED"))

    return _finish(db, c, apply)


def _confirm_inventory(db: Session, c: Claim) -> str:
    try:
        inventory_client.confirm(c.order_id)
    except ServiceUnavailable as exc:
        raise Transient(exc)
    except (inventory_client.ReservationNotFound, inventory_client.InvalidTransition,
            UnexpectedResponse) as exc:
        raise Permanent(f"cannot confirm: {type(exc).__name__} {exc}")

    def apply(db: Session) -> None:
        confirmed = db.execute(update(Order)
                               .where(Order.id == c.order_id, Order.inventory_status == "RESERVED")
                               .values(inventory_status="CONFIRMED").returning(Order.id)).one_or_none()
        if confirmed:
            # Chained in the same transaction: no ORDER_PAID before the inventory is confirmed
            order = db.get(Order, c.order_id)
            add_operation(db, c.order_id, "SEND_NOTIFICATION", "ORDER_PAID",
                          _notification_payload(order, "ORDER_PAID"))

    return _finish(db, c, apply)


def _release_inventory(db: Session, c: Claim) -> str:
    try:
        inventory_client.release(c.order_id)  # an unknown order leaves a tombstone
    except ServiceUnavailable as exc:
        raise Transient(exc)
    except (inventory_client.InvalidTransition, UnexpectedResponse) as exc:
        raise Permanent(f"cannot release: {type(exc).__name__} {exc}")

    def apply(db: Session) -> None:
        # NOT_RESERVED stays as it is: there was nothing to free
        db.execute(update(Order)
                   .where(Order.id == c.order_id, Order.inventory_status.in_(("PENDING", "RESERVED")))
                   .values(inventory_status="RELEASED"))

    return _finish(db, c, apply)


def _send_notification(db: Session, c: Claim) -> str:
    p = c.payload
    try:
        notification_client.send_event(
            order_id=c.order_id, user_id=p["user_id"], event_type=p["event_type"],
            amount=Decimal(p["amount"]) if p.get("amount") else None, currency=p.get("currency"))
    except ServiceUnavailable as exc:
        raise Transient(exc)  # the order is never reverted because of a notification
    except (notification_client.EventConflict, UnexpectedResponse) as exc:
        raise Permanent(f"notification rejected: {type(exc).__name__} {exc}")
    return _finish(db, c, lambda db: None)


HANDLERS = {
    "RESOLVE_PAYMENT": _resolve_payment,
    "CONFIRM_INVENTORY": _confirm_inventory,
    "RELEASE_INVENTORY": _release_inventory,
    "SEND_NOTIFICATION": _send_notification,
}


# --- entry points ---------------------------------------------------------------------

def execute(db: Session, c: Claim) -> str:
    """Runs a claimed operation. Returns DONE, RETRY, FAILED or LOST."""
    try:
        return HANDLERS[c.kind](db, c)
    except Transient as exc:
        return _fail(db, c, exc, permanent=False)
    except Permanent as exc:
        return _fail(db, c, exc, permanent=True)


def run_operation(db: Session, op_id: int, ignore_schedule: bool = False) -> str:
    """Claims and runs one operation. Returns DONE, RETRY, FAILED, SKIPPED or LOST."""
    c = claim(db, op_id, ignore_schedule)
    if c is None:
        return SKIPPED  # done, permanent, not due yet, or another runner holds it
    return execute(db, c)


def run_order_operations(db: Session, order_id: uuid.UUID, ignore_schedule: bool = False) -> None:
    """Runs the pending operations of one order right away, including the ones
    that each operation adds (payment -> confirm -> notify). What cannot be
    done now stays saved for a later retry."""
    attempted: set[int] = set()
    while True:
        ids = db.scalars(select(PendingOperation.id)
                         .where(PendingOperation.order_id == order_id, ELIGIBLE,
                                PendingOperation.id.not_in(attempted) if attempted else true())
                         .order_by(PendingOperation.id)).all()
        db.commit()
        if not ids:
            return
        for op_id in ids:
            attempted.add(op_id)
            run_operation(db, op_id, ignore_schedule)
