import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class AdjustmentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    delta: int = Field(ge=-100_000, le=100_000, description="Units to add (positive) or remove (negative)")
    reason: str = Field(default="", max_length=200)

    @field_validator("delta")
    @classmethod
    def not_zero(cls, value: int) -> int:
        if value == 0:
            raise ValueError("delta cannot be 0")
        return value

    @field_validator("reason")
    @classmethod
    def strip(cls, value: str) -> str:
        return value.strip()


class AdjustmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    product_id: int
    delta: int
    available_before: int
    available_after: int
    admin_user_id: int | None
    reason: str
    created_at: datetime


class StockPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    product_id: int
    available: int


class StockAdmin(StockPublic):
    reserved: int
    updated_at: datetime


class ReservationItemIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_id: int = Field(gt=0)
    quantity: int = Field(gt=0, le=1000)


class ReservationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order_id: uuid.UUID
    items: list[ReservationItemIn] = Field(min_length=1, max_length=50)

    @field_validator("items")
    @classmethod
    def no_repeated_products(cls, items: list[ReservationItemIn]) -> list[ReservationItemIn]:
        ids = [item.product_id for item in items]
        if len(ids) != len(set(ids)):
            raise ValueError("a product cannot appear twice in the same reservation")
        return items


class ReservationItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    product_id: int
    quantity: int


class ReservationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    order_id: uuid.UUID
    status: str
    items: list[ReservationItemOut]
