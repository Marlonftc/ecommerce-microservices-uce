"""Calls to product-service: current prices of active products.

Uses the public catalogue (GET /products?ids=...), which returns only
ACTIVE products. A product that is missing from the answer either does
not exist or is inactive: in both cases it cannot be bought.
"""
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal

from pydantic import BaseModel, ValidationError

from app.clients.base import ServiceClient, UnexpectedResponse, json_body
from app.config import settings

MAX_IDS = 100  # limit of the ids filter in product-service

client = ServiceClient("product-service", settings.product_service_url, internal=False)


@dataclass(frozen=True)
class ProductSnapshot:
    """What an order keeps from a product at the moment of the purchase."""

    id: int
    sku: str
    name: str
    price: Decimal


class _Product(BaseModel):
    id: int
    sku: str
    name: str
    price: Decimal  # arrives as a string ("749.99"): converted without float
    is_active: bool


class _ProductList(BaseModel):
    items: list[_Product]


def get_active_products(ids: Iterable[int]) -> dict[int, ProductSnapshot]:
    """Returns {product_id: snapshot} for the active products among ids."""
    wanted = sorted(set(ids))
    if not wanted:
        return {}
    if len(wanted) > MAX_IDS:
        raise ValueError(f"at most {MAX_IDS} products per request")

    response = client.request("GET", "/products",
                              params={"ids": ",".join(map(str, wanted)), "limit": MAX_IDS})
    if response.status_code != 200:
        raise UnexpectedResponse(client.name, response.status_code)
    try:
        data = _ProductList.model_validate(json_body(client.name, response))
    except ValidationError:
        raise UnexpectedResponse(client.name, 200, "unexpected body")

    return {p.id: ProductSnapshot(p.id, p.sku, p.name, p.price)
            for p in data.items if p.is_active and p.id in wanted}
