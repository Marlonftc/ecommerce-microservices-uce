"""Checkout: from the cart to a reserved order.

  1. Same Idempotency-Key already used by this user -> the original order.
  2. Read the cart (no locks) and the current prices (HTTP, no transaction open).
  3. Short transaction: lock the cart, check its version again, save the order
     as RESERVING with a copy of the products. Commit.
  4. Ask inventory-service to reserve (HTTP, no transaction open).
  5. Apply the answer with a conditional UPDATE ... WHERE status = 'RESERVING':
     an order that something else already moved (for example to CANCELLED)
     is never overwritten.
"""
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.clients import inventory_client, product_client
from app.clients.base import ServiceUnavailable, UnexpectedResponse
from app.config import settings
from app.models import Order, OrderItem
from app.services import cart_service
from app.services.operations import add_operation

logger = logging.getLogger(__name__)


class IdempotencyKeyReused(Exception):
    """The key was used for a checkout of another cart version."""


class CartChanged(Exception):
    pass


class CartEmpty(Exception):
    pass


class CheckoutInProgress(Exception):
    """Another order of this user is still active (CART_LOCKED)."""


class ProductsNotAvailable(Exception):
    def __init__(self, product_ids: list[int]):
        super().__init__(f"products not available: {product_ids}")
        self.product_ids = product_ids


class OrderNotFound(Exception):
    pass


@dataclass(frozen=True)
class CheckoutResult:
    order: Order
    created: bool                       # True when this request created the order
    insufficient_product: int | None = None  # set when the order was cancelled for stock


# --- reading -------------------------------------------------------------------

def _load(db: Session, order_id: uuid.UUID) -> Order:
    return db.scalars(select(Order).where(Order.id == order_id)
                      .execution_options(populate_existing=True)).one()


def items_of(db: Session, order_id: uuid.UUID) -> list[OrderItem]:
    return list(db.scalars(select(OrderItem).where(OrderItem.order_id == order_id)
                           .order_by(OrderItem.product_id)))


def _by_key(db: Session, user_id: int, key: str) -> Order | None:
    return db.scalar(select(Order).where(Order.user_id == user_id, Order.checkout_key == key)
                     .execution_options(populate_existing=True))


def get_for_user(db: Session, order_id: uuid.UUID, user_id: int, is_admin: bool) -> Order:
    query = select(Order).where(Order.id == order_id)
    if not is_admin:
        query = query.where(Order.user_id == user_id)  # another user's order is never loaded
    order = db.scalar(query)
    if order is None:
        raise OrderNotFound
    return order


def list_for_user(db: Session, user_id: int, is_admin: bool, page: int, size: int):
    filters = [] if is_admin else [Order.user_id == user_id]
    total = db.scalar(select(func.count()).select_from(Order).where(*filters))
    orders = list(db.scalars(select(Order).where(*filters)
                             .order_by(Order.created_at.desc(), Order.id.desc())
                             .limit(size).offset((page - 1) * size)))
    return orders, total


# --- checkout ---------------------------------------------------------------------

def checkout(db: Session, user_id: int, key: str, cart_version: int) -> CheckoutResult:
    # 1. A retry with the same key: answer from the original order only
    existing = _by_key(db, user_id, key)
    db.commit()
    if existing is not None:
        return _replay(db, existing, cart_version)

    # 2. Read the cart without locks, then prices without any transaction open
    view = cart_service.read_cart(db, user_id)
    db.commit()
    if view.version != cart_version:
        raise CartChanged
    if not view.items:
        raise CartEmpty
    if view.locked:
        # The active order may be the one created by a simultaneous request
        # with this same key (a double click): then answer with it
        return _replay_or_locked(db, user_id, key, cart_version)
    products = product_client.get_active_products(view.items)  # may raise ServiceUnavailable
    missing = sorted(set(view.items) - set(products))
    if missing:
        raise ProductsNotAvailable(missing)

    # 3. Short transaction with the same lock as every cart change
    try:
        cart = cart_service.lock_cart(db, user_id)
        same_key = _by_key(db, user_id, key)
        if same_key is not None:
            # A simultaneous request with the same key saved it first
            db.commit()
            return _replay(db, same_key, cart_version)
        if cart.version != cart_version:
            raise CartChanged  # the cart changed while prices were being read
        if cart_service.has_active_order(db, user_id):
            raise CheckoutInProgress

        order = Order(id=uuid.uuid4(), user_id=user_id, status="RESERVING", checkout_key=key,
                      cart_version=cart_version, total=Decimal("0.00"))
        total = Decimal("0.00")
        lines = []
        for product_id, quantity in sorted(view.items.items()):
            product = products[product_id]
            subtotal = product.price * quantity  # computed here, never sent by the client
            total += subtotal
            lines.append(OrderItem(order_id=order.id, product_id=product_id, sku=product.sku,
                                   name=product.name, unit_price=product.price,
                                   quantity=quantity, subtotal=subtotal))
        order.total = total
        db.add(order)
        db.flush()
        db.add_all(lines)
        db.commit()
    except IntegrityError:
        # UNIQUE(user_id, checkout_key) or one-active-order index: another request won
        db.rollback()
        return _replay_or_locked(db, user_id, key, cart_version)
    except Exception:
        db.rollback()
        raise

    # 4-5. Reserve and apply the answer
    return _reserve(db, order.id, created=True)


def _replay_or_locked(db: Session, user_id: int, key: str, cart_version: int) -> CheckoutResult:
    """Another order is active. If it carries this key, it is the answer;
    otherwise the user already has a different checkout in progress."""
    order = _by_key(db, user_id, key)
    db.commit()
    if order is None:
        raise CheckoutInProgress
    return _replay(db, order, cart_version)


def _replay(db: Session, order: Order, cart_version: int) -> CheckoutResult:
    if order.cart_version != cart_version:
        raise IdempotencyKeyReused
    if order.status == "RESERVING":
        # The first attempt did not get an answer from inventory: try again
        return _reserve(db, order.id, created=False)
    return CheckoutResult(order, created=False)


def _reserve(db: Session, order_id: uuid.UUID, created: bool) -> CheckoutResult:
    lines = [(i.product_id, i.quantity) for i in items_of(db, order_id)]
    db.commit()  # nothing stays open during the call

    try:
        inventory_client.reserve(order_id, lines)
    except inventory_client.InsufficientStock as exc:
        _cancel(db, order_id, "INSUFFICIENT_STOCK", inventory_status="NOT_RESERVED")
        return CheckoutResult(_load(db, order_id), created, insufficient_product=exc.product_id)
    except inventory_client.ReservationReleased:
        # inventory already released this order (tombstone): it can never be reserved
        _cancel(db, order_id, "RESERVATION_TIMEOUT", inventory_status="RELEASED")
        return CheckoutResult(_load(db, order_id), created)
    except ServiceUnavailable as exc:
        # Timeout or inventory down: the order stays RESERVING. A retry with the
        # same key repeats the (idempotent) reservation.
        logger.warning("order %s stays RESERVING: %s", order_id, exc)
        return CheckoutResult(_load(db, order_id), created)
    except (inventory_client.ReservationMismatch, UnexpectedResponse) as exc:
        # Contract or configuration error: never guess, keep RESERVING and log it
        logger.error("order %s: unexpected inventory answer: %s", order_id, exc)
        raise

    expires_at = datetime.now(UTC) + timedelta(minutes=settings.order_reservation_ttl_minutes)
    db.execute(update(Order)
               .where(Order.id == order_id, Order.status == "RESERVING")
               .values(status="PENDING_PAYMENT", inventory_status="RESERVED", expires_at=expires_at))
    db.commit()
    # If the order was cancelled meanwhile, the UPDATE changed nothing and the
    # RELEASE_INVENTORY saved with that cancellation frees this late reservation
    return CheckoutResult(_load(db, order_id), created)


def _cancel(db: Session, order_id: uuid.UUID, reason: str, inventory_status: str) -> None:
    """Cancels a RESERVING order and, in the same transaction, records the
    release in inventory: if a reservation exists after all (a lost answer),
    it will be freed; if not, inventory leaves a tombstone that rejects a late one."""
    result = db.execute(update(Order)
                        .where(Order.id == order_id, Order.status == "RESERVING")
                        .values(status="CANCELLED", cancel_reason=reason,
                                inventory_status=inventory_status))
    if result.rowcount == 1:
        add_operation(db, order_id, "RELEASE_INVENTORY")
    db.commit()
