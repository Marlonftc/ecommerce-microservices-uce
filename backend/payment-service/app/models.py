import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, DateTime, Integer, Numeric, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

PAYMENT_STATUSES = ("PENDING", "APPROVED", "REJECTED")
SIMULATIONS = ("APPROVE", "REJECT", "SLOW_APPROVE")


class Payment(Base):
    """One simulated payment per order. No card data is ever stored."""

    __tablename__ = "payments"
    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_payments_amount_positive"),
        CheckConstraint("currency = 'USD'", name="ck_payments_currency_usd"),
        CheckConstraint("status IN ('PENDING', 'APPROVED', 'REJECTED')", name="ck_payments_status"),
        CheckConstraint(
            "simulation IN ('APPROVE', 'REJECT', 'SLOW_APPROVE')", name="ck_payments_simulation"
        ),
        # A pending payment has no processing date; a resolved one always has it
        CheckConstraint(
            "(status = 'PENDING') = (processed_at IS NULL)", name="ck_payments_processed_at"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    # One payment per order: the strongest barrier against charging twice
    order_id: Mapped[uuid.UUID] = mapped_column(Uuid, unique=True)
    idempotency_key: Mapped[str] = mapped_column(String(100), unique=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(3), default="USD", server_default="USD")
    # The original requested behaviour, kept to resume a PENDING payment safely
    simulation: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="PENDING", server_default="PENDING")
    failure_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
