"""Cart endpoints against a real PostgreSQL (rolled back) and a fake catalogue.

Test users have ids far from real ones (900101...).
"""
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.models import Order
from tests.conftest import bearer, make_token

ME, OTHER = 900101, 900102


def put(client, product_id, quantity, user=ME, **extra):
    return client.put(f"/cart/items/{product_id}", json={"quantity": quantity, **extra}, headers=bearer(user))


def get(client, user=ME):
    return client.get("/cart", headers=bearer(user)).json()


def start_checkout(db_session, user=ME, status="PENDING_PAYMENT"):
    payment = {}
    if status == "PAYMENT_PENDING":
        payment = dict(payment_key="p", payment_simulation="APPROVE", payment_status="REQUESTED")
    db_session.add(Order(user_id=user, status=status, total=Decimal("10.00"),
                         checkout_key=f"k-{uuid.uuid4()}", cart_version=0, inventory_status="RESERVED",
                         expires_at=datetime.now(UTC) + timedelta(minutes=15), **payment))
    db_session.commit()


# 1-3. Read, add, modify ------------------------------------------------------------

def test_empty_cart(client):
    assert get(client) == {"version": 0, "locked": False, "items": [], "prices_available": True,
                           "estimated_total": "0.00", "currency": "USD"}


def test_add_products(client):
    put(client, 1, 2)
    cart = put(client, 2, 3).json()

    assert cart["version"] == 2
    assert [(i["product_id"], i["quantity"]) for i in cart["items"]] == [(1, 2), (2, 3)]
    assert cart["items"][0] == {"product_id": 1, "quantity": 2, "sku": "SKU-1", "name": "Producto 1",
                                "unit_price": "749.99", "subtotal": "1499.98", "available": True}


def test_change_quantity_sets_the_value(client):
    put(client, 1, 2)
    cart = put(client, 1, 5).json()

    assert cart["items"][0]["quantity"] == 5  # absolute value, not 2 + 5
    assert cart["version"] == 2


# 4. Repeated PUT ----------------------------------------------------------------------

def test_repeated_put_does_not_change_the_version(client):
    first = put(client, 1, 2).json()
    again = put(client, 1, 2)

    assert again.status_code == 200
    assert again.json()["version"] == first["version"] == 1


# 5-7. Remove and empty ---------------------------------------------------------------------

def test_remove_a_product(client):
    put(client, 1, 2)
    put(client, 2, 1)

    cart = client.delete("/cart/items/1", headers=bearer(ME)).json()

    assert [i["product_id"] for i in cart["items"]] == [2]
    assert cart["version"] == 3


def test_removing_a_missing_product_changes_nothing(client):
    put(client, 1, 2)

    first = client.delete("/cart/items/7", headers=bearer(ME))
    again = client.delete("/cart/items/7", headers=bearer(ME))

    assert first.status_code == again.status_code == 200
    assert again.json()["version"] == 1


def test_empty_the_cart(client):
    put(client, 1, 2)
    put(client, 2, 1)

    cart = client.delete("/cart", headers=bearer(ME)).json()

    assert (cart["items"], cart["version"]) == ([], 3)


def test_emptying_an_empty_cart_changes_nothing(client):
    put(client, 1, 2)
    client.delete("/cart", headers=bearer(ME))

    again = client.delete("/cart", headers=bearer(ME)).json()

    assert again["version"] == 2


def test_version_never_restarts(client):
    put(client, 1, 1)
    client.delete("/cart", headers=bearer(ME))

    assert put(client, 1, 1).json()["version"] == 3  # not 1


# 8-9. Validation -------------------------------------------------------------------------

@pytest.mark.parametrize("body", [{"quantity": 0}, {"quantity": 100}, {"quantity": -1},
                                  {"quantity": 1.5}, {}, {"quantity": 1, "price": "1.00"},
                                  {"quantity": 1, "expected_version": -1}])
def test_invalid_bodies_are_rejected(client, body):
    response = client.put("/cart/items/1", json=body, headers=bearer(ME))

    assert response.status_code == 422
    assert get(client)["version"] == 0


@pytest.mark.parametrize("path", ["/cart/items/0", "/cart/items/-3", "/cart/items/abc",
                                  "/cart/items/2147483648"])
def test_invalid_product_ids(client, path):
    assert client.put(path, json={"quantity": 1}, headers=bearer(ME)).status_code == 422


def test_unknown_or_inactive_product_is_rejected(client, catalog):
    del catalog.prices[5]  # inactive or deleted: product-service does not return it

    response = put(client, 5, 1)

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "PRODUCT_NOT_AVAILABLE"
    assert get(client)["items"] == []


# 10. product-service down ----------------------------------------------------------------

def test_put_with_product_service_down_keeps_the_cart(client, catalog):
    put(client, 1, 2)
    catalog.down = True

    response = put(client, 2, 1)

    assert response.status_code == 503
    catalog.down = False
    cart = get(client)
    assert ([i["product_id"] for i in cart["items"]], cart["version"]) == ([1], 1)


def test_get_with_product_service_down_shows_items_without_prices(client, catalog):
    put(client, 1, 2)
    catalog.down = True

    response = client.get("/cart", headers=bearer(ME))

    assert response.status_code == 200
    assert response.json() == {"version": 1, "locked": False, "prices_available": False,
                               "estimated_total": None, "currency": "USD",
                               "items": [{"product_id": 1, "quantity": 2, "sku": None, "name": None,
                                          "unit_price": None, "subtotal": None, "available": None}]}


def test_removing_and_emptying_do_not_need_the_catalogue_to_change(client, catalog):
    put(client, 1, 2)
    put(client, 2, 1)
    catalog.down = True

    assert client.delete("/cart/items/1", headers=bearer(ME)).json()["version"] == 3
    assert client.delete("/cart", headers=bearer(ME)).json()["items"] == []


def test_deactivated_product_stays_visible_but_not_counted(client, catalog):
    put(client, 1, 1)
    put(client, 2, 3)
    del catalog.prices[2]

    cart = get(client)

    assert cart["items"][1]["available"] is False
    assert cart["estimated_total"] == "749.99"


# 11. CART_LOCKED ---------------------------------------------------------------------

@pytest.mark.parametrize("status", ["RESERVING", "PENDING_PAYMENT", "PAYMENT_PENDING"])
def test_cart_is_locked_during_checkout(client, db_session, status):
    put(client, 1, 2)
    start_checkout(db_session, status=status)

    for response in (put(client, 2, 1), client.delete("/cart/items/1", headers=bearer(ME)),
                     client.delete("/cart", headers=bearer(ME))):
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "CART_LOCKED"

    cart = get(client)
    assert (cart["locked"], cart["version"], len(cart["items"])) == (True, 1, 1)


@pytest.mark.parametrize("status", ["PAID", "PAYMENT_REJECTED", "CANCELLED"])
def test_finished_orders_do_not_lock_the_cart(client, db_session, status):
    db_session.add(Order(user_id=ME, status=status, total=Decimal("10.00"), checkout_key="k",
                         cart_version=0, cancel_reason="USER" if status == "CANCELLED" else None,
                         payment_key=None if status == "CANCELLED" else "p",
                         payment_simulation=None if status == "CANCELLED" else "APPROVE",
                         payment_status={"PAID": "APPROVED", "PAYMENT_REJECTED": "REJECTED"}.get(status)))
    db_session.commit()

    assert put(client, 1, 1).status_code == 200


# 12. CART_FULL -----------------------------------------------------------------------

def test_cart_holds_at_most_50_products(client):
    for product_id in range(1, 51):
        assert put(client, product_id, 1).status_code == 200

    response = put(client, 51, 1)

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "CART_FULL"
    assert put(client, 50, 3).status_code == 200  # changing a product already in it is fine


# 13. Isolation and authentication -----------------------------------------------------------

def test_each_user_sees_only_their_cart(client):
    put(client, 1, 2, user=ME)
    put(client, 3, 1, user=OTHER)

    assert [i["product_id"] for i in get(client, ME)["items"]] == [1]
    assert [i["product_id"] for i in get(client, OTHER)["items"]] == [3]


def test_one_user_cannot_change_another_users_cart(client):
    put(client, 1, 2, user=OTHER)

    client.delete("/cart", headers=bearer(ME))  # only empties ME's cart

    assert len(get(client, OTHER)["items"]) == 1


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer abc"},
                                     {"Authorization": f"Bearer {make_token(ME, minutes=-1)}"}])
def test_token_is_required(client, headers):
    assert client.get("/cart", headers=headers).status_code == 401
    assert client.put("/cart/items/1", json={"quantity": 1}, headers=headers).status_code == 401
    assert client.delete("/cart", headers=headers).status_code == 401


# 15. Stale versions ----------------------------------------------------------------

def test_stale_version_is_rejected_without_changes(client):
    put(client, 1, 2)                    # version 1
    put(client, 2, 1)                    # version 2

    response = put(client, 3, 1, expected_version=1)

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "CART_CHANGED"
    cart = get(client)
    assert (cart["version"], len(cart["items"])) == (2, 2)


def test_current_version_is_accepted(client):
    put(client, 1, 2)

    assert put(client, 1, 4, expected_version=1).json()["version"] == 2


def test_stale_version_on_delete(client):
    put(client, 1, 2)
    put(client, 2, 1)

    assert client.delete("/cart/items/1?expected_version=1", headers=bearer(ME)).status_code == 409
    assert client.delete("/cart?expected_version=0", headers=bearer(ME)).status_code == 409
    assert client.delete("/cart?expected_version=2", headers=bearer(ME)).status_code == 200


def test_retry_of_an_applied_change_is_not_a_conflict(client):
    # The first PUT worked but its answer was lost; the client retries with the old version
    put(client, 1, 2, expected_version=0)

    again = put(client, 1, 2, expected_version=0)

    assert again.status_code == 200
    assert again.json()["version"] == 1


# 16. Money --------------------------------------------------------------------------------

def test_totals_are_exact_decimals(client):
    put(client, 2, 3)   # 0.10 x 3: 0.30000000000000004 with float
    put(client, 1, 1)   # 749.99

    cart = get(client)

    assert cart["items"][1]["subtotal"] == "0.30"
    assert Decimal(cart["estimated_total"]) == Decimal("750.29")


# 17. Version only grows with real changes ---------------------------------------------------

def test_version_only_grows_with_real_changes(client):
    versions = [put(client, 1, 2).json()["version"],                          # change
                put(client, 1, 2).json()["version"],                          # same
                client.delete("/cart/items/9", headers=bearer(ME)).json()["version"],  # missing
                put(client, 1, 3).json()["version"],                          # change
                client.delete("/cart", headers=bearer(ME)).json()["version"],  # change
                client.delete("/cart", headers=bearer(ME)).json()["version"]]  # already empty

    assert versions == [1, 1, 1, 2, 3, 3]
