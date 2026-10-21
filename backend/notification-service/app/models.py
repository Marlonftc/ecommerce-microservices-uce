import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, DateTime, Index, Integer, Numeric, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

NOTIFICATION_TYPES = ("ORDER_PAID", "PAYMENT_REJECTED", "ORDER_CANCELLED")


class Notification(Base):
    """A message for one user about one of their orders.

    user_id and order_id belong to other services (user-service and
    order-service): they are stored as plain values, without foreign keys.
    """

    __tablename__ = "notifications"
    __table_args__ = (
        CheckConstraint(
            "type IN ('ORDER_PAID', 'PAYMENT_REJECTED', 'ORDER_CANCELLED')",
            name="ck_notifications_type",
        ),
        # Both empty, or both present with a positive amount in USD.
        # Written with IS NOT NULL so the check never evaluates to NULL:
        # a CHECK that returns NULL is accepted by PostgreSQL.
        CheckConstraint(
            "(amount IS NULL AND currency IS NULL)"
            " OR (amount IS NOT NULL AND currency IS NOT NULL"
            " AND amount > 0 AND currency = 'USD')",
            name="ck_notifications_amount_currency",
        ),
        # A read notification always has a read date; an unread one never has it
        CheckConstraint("is_read = (read_at IS NOT NULL)", name="ck_notifications_read_at"),
        # Each user's inbox, newest first, page by page
        Index("ix_notifications_user_id_created_at", "user_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    # Sent by order-service as "<order_id>:<type>"; repeats of the same event are rejected here
    event_id: Mapped[str] = mapped_column(String(100), unique=True)
    user_id: Mapped[int] = mapped_column(Integer)
    order_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    type: Mapped[str] = mapped_column(String(30))
    amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    # Built by this service from its own templates (not part of the event data)
    title: Mapped[str] = mapped_column(String(200))
    message: Mapped[str] = mapped_column(String(1000))
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
