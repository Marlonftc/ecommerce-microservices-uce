import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Simulation = Literal["APPROVE", "REJECT", "SLOW_APPROVE"]


class PaymentIn(BaseModel):
    """Sent by order-service. No card data exists in this simulation."""

    model_config = ConfigDict(extra="forbid")

    order_id: uuid.UUID
    user_id: int = Field(gt=0)
    # Exact decimal: more than 0, at most 2 decimals. PostgreSQL would round
    # 10.999 silently, so the precision must be checked here.
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2, examples=["899.99"])
    currency: Literal["USD"] = "USD"
    simulation: Simulation = "APPROVE"


class PaymentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    order_id: uuid.UUID
    user_id: int
    amount: Decimal
    currency: str
    status: str
    simulation: str
    failure_reason: str | None
    processed_at: datetime | None
    created_at: datetime
