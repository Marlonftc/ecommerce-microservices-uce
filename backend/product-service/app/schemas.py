import re
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

SKU_PATTERN = re.compile(r"^[A-Z0-9]+(-[A-Z0-9]+)*$")
CATEGORY_PATTERN = re.compile(r"^[a-z0-9áéíóúñü]+(-[a-z0-9áéíóúñü]+)*$")


def normalize_category(value: str) -> str:
    """'  Ropa Deportiva ' -> 'ropa-deportiva'"""
    return "-".join(value.strip().lower().split())


class ProductIn(BaseModel):
    """Data sent by the admin to create or replace a product."""

    model_config = ConfigDict(extra="forbid")

    sku: str = Field(min_length=3, max_length=32, examples=["LAP-003"])
    name: str = Field(min_length=2, max_length=120)
    description: str = Field(default="", max_length=2000)
    category: str = Field(min_length=2, max_length=50, examples=["laptops"])
    # Exact decimal: more than 0, at most 2 decimals, at most 12 digits
    price: Decimal = Field(gt=0, max_digits=12, decimal_places=2, examples=["899.99"])

    @field_validator("sku")
    @classmethod
    def normalize_sku(cls, value: str) -> str:
        value = value.strip().upper()
        if not SKU_PATTERN.match(value):
            raise ValueError("sku must contain only letters, numbers and hyphens")
        return value

    @field_validator("name", "description")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("category")
    @classmethod
    def validate_category(cls, value: str) -> str:
        value = normalize_category(value)
        if not CATEGORY_PATTERN.match(value):
            raise ValueError("category must contain only letters, numbers and hyphens")
        return value


class ProductUpdate(ProductIn):
    """A full replacement. is_active allows the admin to reactivate a product."""

    is_active: bool = True


class ProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    sku: str
    name: str
    description: str
    category: str
    price: Decimal  # sent as a string in JSON ("899.99") to keep it exact
    is_active: bool
    created_at: datetime
    updated_at: datetime


class ProductList(BaseModel):
    items: list[ProductOut]
    total: int
    limit: int
    offset: int
