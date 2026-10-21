import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

NotificationType = Literal["ORDER_PAID", "PAYMENT_REJECTED", "ORDER_CANCELLED"]

# users.id in user-service is a PostgreSQL INTEGER (32 bits)
MAX_USER_ID = 2_147_483_647


class NotificationEventIn(BaseModel):
    """Event sent by order-service. Only business data: the texts are built here."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [{
            "event_id": "3f1c2a9e-8d4b-4c7a-9e21-5b6d7f8a9c01:ORDER_PAID",
            "user_id": 7,
            "order_id": "3f1c2a9e-8d4b-4c7a-9e21-5b6d7f8a9c01",
            "type": "ORDER_PAID",
            "amount": "149.90",
            "currency": "USD",
        }]},
    )

    event_id: str = Field(min_length=1, max_length=100)
    user_id: int = Field(gt=0, le=MAX_USER_ID)
    order_id: uuid.UUID
    type: NotificationType
    # Exact decimal, at most 2 decimals: 10.005 is rejected, never rounded
    amount: Decimal | None = Field(default=None, gt=0, max_digits=12, decimal_places=2)
    currency: Literal["USD"] | None = None

    @model_validator(mode="after")
    def check_event(self) -> Self:
        if self.event_id != f"{self.order_id}:{self.type}":
            raise ValueError("event_id must be '<order_id>:<type>' with the same order_id and type")
        if (self.amount is None) != (self.currency is None):
            raise ValueError("amount and currency must be sent together")
        if self.type in ("ORDER_PAID", "PAYMENT_REJECTED") and self.amount is None:
            raise ValueError(f"{self.type} requires amount and currency")
        return self


class NotificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    event_id: str
    user_id: int
    order_id: uuid.UUID
    type: str
    amount: Decimal | None
    currency: str | None
    title: str
    message: str
    is_read: bool
    read_at: datetime | None
    created_at: datetime


class NotificationPage(BaseModel):
    items: list[NotificationOut]
    total: int = Field(description="Notifications matching the filters, in all pages")
    page: int
    size: int
