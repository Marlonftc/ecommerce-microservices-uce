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
