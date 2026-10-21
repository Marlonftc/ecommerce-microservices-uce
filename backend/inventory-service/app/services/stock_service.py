from collections.abc import Callable

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Stock, StockAdjustment


class ProductNotFound(Exception):
    pass


class NotEnoughStock(Exception):
    pass


class IdempotencyKeyReused(Exception):
    """The same key was used before with a different request."""


def get_stock(db: Session, product_id: int) -> Stock | None:
    return db.get(Stock, product_id)


def get_many(db: Session, product_ids: list[int]) -> list[Stock]:
    return list(db.scalars(select(Stock).where(Stock.product_id.in_(product_ids)).order_by(Stock.product_id)))


def _find_by_key(db: Session, key: str) -> StockAdjustment | None:
    return db.scalar(select(StockAdjustment).where(StockAdjustment.idempotency_key == key))


def _replay(previous: StockAdjustment, product_id: int, delta: int, reason: str) -> StockAdjustment:
    if (previous.product_id, previous.delta, previous.reason) != (product_id, delta, reason):
        raise IdempotencyKeyReused
    return previous


def adjust_stock(
    db: Session,
    *,
    product_id: int,
    delta: int,
    reason: str,
    admin_user_id: int,
    idempotency_key: str,
    product_exists: Callable[[int], bool],
) -> tuple[StockAdjustment, bool]:
    """Adds or removes units. Returns (history entry, True if it was applied now).

    A retried request with the same key returns the first result without
    changing the stock again.
    """
    previous = _find_by_key(db, idempotency_key)
    if previous is not None:
        return _replay(previous, product_id, delta, reason), False

    # Called before touching the database: no row stays locked while
    # waiting for another service over the network.
    if not product_exists(product_id):
        raise ProductNotFound

    try:
        # First adjustment of a product: create its stock row with 0 units
        db.execute(insert(Stock).values(product_id=product_id, available=0).on_conflict_do_nothing())

        # Relative and atomic: computed on the current value, in one statement.
        # It only applies if the result is not negative. "reserved" is not touched.
        available_after = db.scalar(
            update(Stock)
            .where(Stock.product_id == product_id, Stock.available + delta >= 0)
            .values(available=Stock.available + delta)
            .returning(Stock.available)
        )
        if available_after is None:
            db.rollback()
            raise NotEnoughStock

        adjustment = StockAdjustment(
            idempotency_key=idempotency_key,
            product_id=product_id,
            delta=delta,
            available_before=available_after - delta,
            available_after=available_after,
            admin_user_id=admin_user_id,
            reason=reason,
        )
        db.add(adjustment)
        db.commit()
    except IntegrityError:
        # Another request with the same key was committed first. Everything
        # done here (including the stock change) is undone.
        db.rollback()
        previous = _find_by_key(db, idempotency_key)
        if previous is None:
            raise
        return _replay(previous, product_id, delta, reason), False

    db.refresh(adjustment)
    return adjustment, True


def list_adjustments(db: Session, product_id: int, limit: int, offset: int) -> list[StockAdjustment]:
    return list(db.scalars(
        select(StockAdjustment)
        .where(StockAdjustment.product_id == product_id)
        .order_by(StockAdjustment.id.desc())
        .limit(limit)
        .offset(offset)
    ))
