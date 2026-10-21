import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

RESERVATION_STATUSES = ("RESERVED", "CONFIRMED", "RELEASED")


class Stock(Base):
    """Units of one product. product_id comes from product-service (no foreign key)."""

    __tablename__ = "stock"
    __table_args__ = (
        CheckConstraint("available >= 0", name="ck_stock_available_not_negative"),
        CheckConstraint("reserved >= 0", name="ck_stock_reserved_not_negative"),
    )

    product_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    available: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    reserved: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Reservation(Base):
    """Units set aside for one order. One reservation per order_id."""

    __tablename__ = "reservations"
    __table_args__ = (
        CheckConstraint(
            "status IN ('RESERVED', 'CONFIRMED', 'RELEASED')", name="ck_reservations_status"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[uuid.UUID] = mapped_column(Uuid, unique=True, index=True)
    status: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    items: Mapped[list["ReservationItem"]] = relationship(
        back_populates="reservation", cascade="all, delete-orphan", order_by="ReservationItem.product_id"
    )


class ReservationItem(Base):
    __tablename__ = "reservation_items"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_reservation_items_quantity_positive"),
        UniqueConstraint("reservation_id", "product_id", name="uq_reservation_items_product"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Same service and same database: a foreign key is correct here
    reservation_id: Mapped[int] = mapped_column(ForeignKey("reservations.id", ondelete="CASCADE"))
    product_id: Mapped[int] = mapped_column(Integer)
    quantity: Mapped[int] = mapped_column(Integer)

    reservation: Mapped[Reservation] = relationship(back_populates="items")


class StockAdjustment(Base):
    """History of stock changes made by an admin (or by the initial load).

    The unique idempotency_key also prevents applying a retried request twice.
    """

    __tablename__ = "stock_adjustments"
    __table_args__ = (
        CheckConstraint("delta <> 0", name="ck_stock_adjustments_delta_not_zero"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(100), unique=True)
    product_id: Mapped[int] = mapped_column(Integer, index=True)
    delta: Mapped[int] = mapped_column(Integer)
    available_before: Mapped[int] = mapped_column(Integer)
    available_after: Mapped[int] = mapped_column(Integer)
    # Empty for the initial load
    admin_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reason: Mapped[str] = mapped_column(String(200), default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
