"""Tables of order-service.

user_id and product_id belong to user-service and product-service: they are
stored as plain INTEGER values, without foreign keys to other databases.
Foreign keys exist only between the tables of this service.
"""
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger, CheckConstraint, DateTime, ForeignKey, Index, Integer, Numeric, String, UniqueConstraint, Uuid,
    func, text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

ORDER_STATUSES = ("RESERVING", "PENDING_PAYMENT", "PAYMENT_PENDING", "PAID", "PAYMENT_REJECTED", "CANCELLED")
# A checkout in progress: the cart stays locked and the user cannot start another one
ACTIVE_STATUSES = ("RESERVING", "PENDING_PAYMENT", "PAYMENT_PENDING")
CANCEL_REASONS = ("USER", "EXPIRED", "INSUFFICIENT_STOCK", "RESERVATION_TIMEOUT")
INVENTORY_STATUSES = ("PENDING", "RESERVED", "CONFIRMED", "RELEASED", "NOT_RESERVED")
PAYMENT_STATUSES = ("REQUESTED", "APPROVED", "REJECTED")
SIMULATIONS = ("APPROVE", "REJECT", "SLOW_APPROVE")
OPERATION_KINDS = ("RESOLVE_PAYMENT", "CONFIRM_INVENTORY", "RELEASE_INVENTORY", "SEND_NOTIFICATION")
OPERATION_STATUSES = ("PENDING", "DONE", "FAILED")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


ACTIVE_ORDER_FILTER = _in("status", ACTIVE_STATUSES)


class Cart(Base):
    """One persistent cart per user. version grows with every change of its items
    and is never reset, so a checkout can tell exactly which cart it saw."""

    __tablename__ = "carts"
    __table_args__ = (
        CheckConstraint("user_id > 0", name="ck_carts_user_id_positive"),
        CheckConstraint("version >= 0", name="ck_carts_version_not_negative"),
    )

    user_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class CartItem(Base):
    """Products and quantities only: prices are read from product-service at checkout."""

    __tablename__ = "cart_items"
    __table_args__ = (
        CheckConstraint("product_id > 0", name="ck_cart_items_product_id_positive"),
        CheckConstraint("quantity BETWEEN 1 AND 99", name="ck_cart_items_quantity_range"),
    )

    user_id: Mapped[int] = mapped_column(
        ForeignKey("carts.user_id", ondelete="CASCADE", name="fk_cart_items_user_id_carts"), primary_key=True)
    product_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    quantity: Mapped[int] = mapped_column(Integer)


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        CheckConstraint("user_id > 0", name="ck_orders_user_id_positive"),
        CheckConstraint(_in("status", ORDER_STATUSES), name="ck_orders_status"),
        CheckConstraint("currency = 'USD'", name="ck_orders_currency_usd"),
        CheckConstraint("total > 0", name="ck_orders_total_positive"),
        CheckConstraint("cart_version >= 0", name="ck_orders_cart_version_not_negative"),
        CheckConstraint(_in("inventory_status", INVENTORY_STATUSES), name="ck_orders_inventory_status"),
        # A cancelled order always says why; any other order has no reason
        CheckConstraint(_in("cancel_reason", CANCEL_REASONS), name="ck_orders_cancel_reason"),
        CheckConstraint("(status = 'CANCELLED') = (cancel_reason IS NOT NULL)",
                        name="ck_orders_cancel_reason_only_when_cancelled"),
        # Waiting for payment means a deadline exists. It is kept afterwards as history.
        CheckConstraint("status <> 'PENDING_PAYMENT' OR expires_at IS NOT NULL",
                        name="ck_orders_expires_at_when_pending_payment"),
        # Payment intent: key, mode and status are recorded together, or not at all.
        # payment_id may stay empty while the result is unknown (timeout).
        CheckConstraint(_in("payment_status", PAYMENT_STATUSES), name="ck_orders_payment_status"),
        CheckConstraint(_in("payment_simulation", SIMULATIONS), name="ck_orders_payment_simulation"),
        CheckConstraint("(payment_key IS NULL) = (payment_simulation IS NULL) "
                        "AND (payment_key IS NULL) = (payment_status IS NULL)",
                        name="ck_orders_payment_intent_complete"),
        CheckConstraint("status NOT IN ('PAYMENT_PENDING', 'PAID', 'PAYMENT_REJECTED') OR payment_key IS NOT NULL",
                        name="ck_orders_payment_intent_recorded"),
        # COALESCE: with payment_status NULL the comparison would be NULL, and a
        # CHECK that evaluates to NULL is accepted by PostgreSQL
        CheckConstraint("status <> 'PAID' OR COALESCE(payment_status, '') = 'APPROVED'",
                        name="ck_orders_paid_is_approved"),
        CheckConstraint("status <> 'PAYMENT_REJECTED' OR COALESCE(payment_status, '') = 'REJECTED'",
                        name="ck_orders_rejected_is_rejected"),
        # Retrying a checkout with the same key finds the original order
        Index("uq_orders_user_id_checkout_key", "user_id", "checkout_key", unique=True),
        # At most one checkout in progress per user, guaranteed by the database
        Index("uq_orders_one_active_per_user", "user_id", unique=True,
              postgresql_where=text(ACTIVE_ORDER_FILTER)),
        Index("ix_orders_user_id_created_at", "user_id", "created_at"),
        # Worker: expired PENDING_PAYMENT and old RESERVING orders
        Index("ix_orders_status_updated_at", "status", "updated_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20))
    currency: Mapped[str] = mapped_column(String(3), default="USD", server_default="USD")
    # Sum of the item subtotals, computed in the backend
    total: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    # Idempotency of POST /orders: the customer's key and the cart version it was sent with
    checkout_key: Mapped[str] = mapped_column(String(100))
    cart_version: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_reason: Mapped[str | None] = mapped_column(String(30), nullable=True)
    # PAID with RESERVED here means the confirmation in inventory is still pending
    inventory_status: Mapped[str] = mapped_column(String(20), default="PENDING", server_default="PENDING")
    # Payment intent. payment_key is the customer's key; the key sent to
    # payment-service is derived from the order id and is not stored here.
    payment_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    payment_simulation: Mapped[str | None] = mapped_column(String(20), nullable=True)
    payment_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    payment_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class OrderItem(Base):
    """Copy of the product at the moment of the purchase: later price or name
    changes in product-service do not change the order."""

    __tablename__ = "order_items"
    __table_args__ = (
        CheckConstraint("product_id > 0", name="ck_order_items_product_id_positive"),
        CheckConstraint("quantity BETWEEN 1 AND 99", name="ck_order_items_quantity_range"),
        CheckConstraint("unit_price > 0", name="ck_order_items_unit_price_positive"),
        CheckConstraint("subtotal = unit_price * quantity", name="ck_order_items_subtotal"),
    )

    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE", name="fk_order_items_order_id_orders"), primary_key=True)
    product_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    # Same lengths as product-service
    sku: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(120))
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    quantity: Mapped[int] = mapped_column(Integer)
    subtotal: Mapped[Decimal] = mapped_column(Numeric(12, 2))


class PendingOperation(Base):
    """Work that must reach another service, saved in the same transaction as
    the order change that requires it (outbox). It survives restarts.

    The payload has only business data needed to repeat the exact call:
    never tokens or the internal key, which are added when the call is made.
    """

    __tablename__ = "pending_operations"
    __table_args__ = (
        CheckConstraint(_in("kind", OPERATION_KINDS), name="ck_pending_operations_kind"),
        CheckConstraint(_in("status", OPERATION_STATUSES), name="ck_pending_operations_status"),
        CheckConstraint("attempts >= 0", name="ck_pending_operations_attempts_not_negative"),
        CheckConstraint("error_kind IN ('TRANSIENT', 'PERMANENT')", name="ck_pending_operations_error_kind"),
        CheckConstraint("(status = 'DONE') = (completed_at IS NOT NULL)",
                        name="ck_pending_operations_completed_at_when_done"),
        # A lease is a pair: the deadline and the token of the worker that holds it
        CheckConstraint("(locked_until IS NULL) = (lease_token IS NULL)",
                        name="ck_pending_operations_lease_complete"),
        UniqueConstraint("operation_key", name="uq_pending_operations_operation_key"),
        # Operations the worker may take, oldest due first
        Index("ix_pending_operations_due", "next_attempt_at",
              postgresql_where=text("status <> 'DONE'")),
        Index("ix_pending_operations_order_id", "order_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE", name="fk_pending_operations_order_id_orders"))
    kind: Mapped[str] = mapped_column(String(30))
    # Deterministic: "<order_id>:CONFIRM_INVENTORY", or the event_id of a notification
    operation_key: Mapped[str] = mapped_column(String(150))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    status: Mapped[str] = mapped_column(String(20), default="PENDING", server_default="PENDING")
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    error_kind: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # Lease: a worker owns the operation until locked_until. Only the worker
    # holding lease_token may write the result, so a worker whose lease
    # expired cannot overwrite the work of the next one.
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_token: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
