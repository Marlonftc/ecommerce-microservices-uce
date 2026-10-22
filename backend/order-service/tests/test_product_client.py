from decimal import Decimal

import pytest

from app.clients import product_client
from app.clients.base import ServiceUnavailable


def product(id, price, active=True):
    # Same shape as ProductOut in product-service
    return {"id": id, "sku": f"SKU-{id}", "name": f"Producto {id}", "description": "",
            "category": "laptops", "price": price, "is_active": active,
            "created_at": "2026-10-21T10:00:00Z", "updated_at": "2026-10-21T10:00:00Z"}


def page(*items):
    return (200, {"items": list(items), "total": len(items), "limit": 100, "offset": 0})


def test_asks_for_all_ids_in_one_call(fake):
    service = fake(product_client, page())

    product_client.get_active_products([3, 1, 3, 2])

    assert service.last.url.path == "/products"
    assert service.last.url.params["ids"] == "1,2,3"  # sorted, no repeats
    assert service.last.url.params["limit"] == "100"


def test_prices_are_exact_decimals(fake):
    fake(product_client, page(product(1, "749.99"), product(2, "0.10")))

    result = product_client.get_active_products([1, 2])

    assert result[1].price == Decimal("749.99")
    assert result[2].price == Decimal("0.10")
    assert (result[1].sku, result[1].name) == ("SKU-1", "Producto 1")


def test_missing_products_are_not_in_the_result(fake):
    # product-service omits inactive and unknown products from the public list
    fake(product_client, page(product(1, "10.00")))

    assert set(product_client.get_active_products([1, 99])) == {1}


def test_inactive_products_are_ignored_even_if_returned(fake):
    fake(product_client, page(product(1, "10.00", active=False)))

    assert product_client.get_active_products([1]) == {}


def test_no_ids_means_no_call(fake):
    service = fake(product_client, page())

    assert product_client.get_active_products([]) == {}
    assert service.requests == []


def test_more_than_100_ids_is_rejected_before_calling(fake):
    service = fake(product_client, page())

    with pytest.raises(ValueError):
        product_client.get_active_products(range(1, 102))
    assert service.requests == []


def test_product_service_down(fake):
    fake(product_client, (503, {"detail": "down"}))

    with pytest.raises(ServiceUnavailable):
        product_client.get_active_products([1])
