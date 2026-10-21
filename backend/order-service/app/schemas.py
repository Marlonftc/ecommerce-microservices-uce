import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class CartItemIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Absolute quantity, not an increment: repeating the request gives the same cart
    quantity: int = Field(ge=1, le=99)
    # Version the client last saw. If the cart changed since then, 409 CART_CHANGED.
    expected_version: int | None = Field(default=None, ge=0)


class CartItemOut(BaseModel):
    product_id: int
    quantity: int
    # Filled from product-service when it answers; null otherwise
    sku: str | None = None
    name: str | None = None
    unit_price: Decimal | None = None
    subtotal: Decimal | None = None
    # False if the product is no longer active in the catalogue
    available: bool | None = None


class CartOut(BaseModel):
    version: int
    # True while a checkout is in progress: the cart cannot be modified
    locked: bool
    items: list[CartItemOut]
    prices_available: bool
    # Estimated with current prices; the order recalculates them at checkout
    estimated_total: Decimal | None
    currency: str = "USD"


class CheckoutIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # The cart version the customer saw when pressing "buy"
    cart_version: int = Field(ge=0)


class OrderItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    product_id: int
    sku: str
    name: str
    unit_price: Decimal
    quantity: int
    subtotal: Decimal


class OrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: int
    status: str
    inventory_status: str
    cancel_reason: str | None
    payment_status: str | None
    total: Decimal
    currency: str
    cart_version: int
    expires_at: datetime | None
    created_at: datetime
    updated_at: datetime
    items: list[OrderItemOut]


class OrderPage(BaseModel):
    items: list[OrderOut]
    total: int = Field(description="Orders matching the filters, in all pages")
    page: int
    size: int
