"""Cart of each user.

Every change runs in one short transaction:
  1. create the cart row if it does not exist (INSERT ... ON CONFLICT DO NOTHING)
  2. lock it (SELECT ... FOR UPDATE): changes of the same cart wait in line
  3. refuse if a checkout is in progress (CART_LOCKED)
  4. compare with expected_version, apply the change, version + 1
No other service is called while the row is locked. The checkout (stage 4)
uses the same lock_cart() before saving an order.
"""
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import delete, exists, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import ACTIVE_STATUSES, Cart, CartItem, Order

MAX_PRODUCTS = 50  # a reservation in inventory-service accepts at most 50 products


class CartLocked(Exception):
    """A checkout is in progress for this user."""


class CartChanged(Exception):
    """expected_version is older than the current cart."""


class CartFull(Exception):
    pass


@dataclass(frozen=True)
class CartView:
    version: int
    locked: bool
    items: dict[int, int]  # product_id -> quantity


def lock_cart(db: Session, user_id: int) -> Cart:
    """Creates the cart if needed and locks its row until the transaction ends."""
    db.execute(insert(Cart).values(user_id=user_id).on_conflict_do_nothing(index_elements=["user_id"]))
    return db.scalars(select(Cart).where(Cart.user_id == user_id).with_for_update()
                      .execution_options(populate_existing=True)).one()


def has_active_order(db: Session, user_id: int) -> bool:
    return db.scalar(select(exists().where(Order.user_id == user_id,
                                           Order.status.in_(ACTIVE_STATUSES))))


def _items(db: Session, user_id: int) -> dict[int, int]:
    rows = db.execute(select(CartItem.product_id, CartItem.quantity)
                      .where(CartItem.user_id == user_id))
    return dict(rows.all())


def read_cart(db: Session, user_id: int) -> CartView:
    version = db.scalar(select(Cart.version).where(Cart.user_id == user_id))
    return CartView(version or 0, has_active_order(db, user_id), _items(db, user_id))


def _modify(db: Session, user_id: int, expected_version: int | None,
            change: Callable[[dict[int, int]], dict[int, int]]) -> CartView:
    try:
        cart = lock_cart(db, user_id)
        if has_active_order(db, user_id):
            raise CartLocked
        current = _items(db, user_id)
        target = change(dict(current))

        if target == current:
            # Nothing to change (for example a repeated request): the version
            # stays the same and a stale expected_version is not a conflict
            db.commit()
            return CartView(cart.version, False, current)
        if expected_version is not None and expected_version != cart.version:
            raise CartChanged

        removed = current.keys() - target.keys()
        if removed:
            db.execute(delete(CartItem).where(CartItem.user_id == user_id,
                                              CartItem.product_id.in_(removed)))
        for product_id, quantity in target.items():
            if current.get(product_id) != quantity:
                db.execute(insert(CartItem)
                           .values(user_id=user_id, product_id=product_id, quantity=quantity)
                           .on_conflict_do_update(index_elements=["user_id", "product_id"],
                                                  set_={"quantity": quantity}))
        new_version = db.scalar(update(Cart).where(Cart.user_id == user_id)
                                .values(version=Cart.version + 1).returning(Cart.version))
        db.commit()
        return CartView(new_version, False, target)
    except Exception:
        db.rollback()
        raise


def set_quantity(db: Session, user_id: int, product_id: int, quantity: int,
                 expected_version: int | None = None) -> CartView:
    def change(items: dict[int, int]) -> dict[int, int]:
        if product_id not in items and len(items) >= MAX_PRODUCTS:
            raise CartFull
        items[product_id] = quantity
        return items
    return _modify(db, user_id, expected_version, change)


def remove_item(db: Session, user_id: int, product_id: int,
                expected_version: int | None = None) -> CartView:
    def change(items: dict[int, int]) -> dict[int, int]:
        items.pop(product_id, None)  # removing a missing product changes nothing
        return items
    return _modify(db, user_id, expected_version, change)


def clear(db: Session, user_id: int, expected_version: int | None = None) -> CartView:
    return _modify(db, user_id, expected_version, lambda items: {})


def empty_locked_cart(db: Session, user_id: int) -> None:
    """Empties a cart whose row the caller already locked (payment approved).
    Runs inside the caller's transaction; the version grows only if items existed."""
    removed = db.execute(delete(CartItem).where(CartItem.user_id == user_id)).rowcount
    if removed:
        db.execute(update(Cart).where(Cart.user_id == user_id).values(version=Cart.version + 1))
