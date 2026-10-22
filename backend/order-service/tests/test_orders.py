"""POST /orders and order queries against a real PostgreSQL (rolled back),
with product-service and inventory-service simulated.

Test users have ids far from real ones (900301...).
"""
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select, update

from app.models import Order, PendingOperation
from tests.conftest import bearer

ME, OTHER = 900301, 900302


def fill_cart(client, user=ME, items=((1, 2), (2, 3))):
    version = None
    for product_id, quantity in items:
        version = client.put(f"/cart/items/{product_id}", json={"quantity": quantity},
                             headers=bearer(user)).json()["version"]
    return version


def checkout(client, version, key=None, user=ME):
    return client.post("/orders", json={"cart_version": version},
                       headers={**bearer(user), "Idempotency-Key": key or f"key-{uuid.uuid4()}"})


def operations(db_session, order_id):
    return [(o.kind, o.operation_key, o.status) for o in
            db_session.scalars(select(PendingOperation).where(PendingOperation.order_id == uuid.UUID(order_id)))]


# 1. Successful checkout --------------------------------------------------------

def test_successful_checkout(client, inventory):
    version = fill_cart(client)

    response = checkout(client, version)

    assert response.status_code == 201
    order = response.json()
    assert (order["status"], order["inventory_status"], order["payment_status"]) == (
        "PENDING_PAYMENT", "RESERVED", None)
    assert response.headers["Location"] == f"/orders/{order['id']}"
    expires = datetime.fromisoformat(order["expires_at"])
    assert timedelta(minutes=14) < expires - datetime.now(UTC) <= timedelta(minutes=15)
    # inventory received the same order id and the products of the cart
    assert inventory.reserved == {order["id"]: [{"product_id": 1, "quantity": 2},
                                                {"product_id": 2, "quantity": 3}]}


def test_cart_is_locked_after_checkout(client):
    version = fill_cart(client)
    checkout(client, version)

    assert client.get("/cart", headers=bearer(ME)).json()["locked"] is True
    assert client.put("/cart/items/3", json={"quantity": 1}, headers=bearer(ME)).status_code == 409


# 12-13. Exact totals and snapshots ----------------------------------------------------

def test_totals_are_computed_in_the_backend_with_decimal(client):
    version = fill_cart(client)            # 749.99 x 2 + 0.10 x 3

    order = checkout(client, version).json()

    assert order["items"] == [
        {"product_id": 1, "sku": "SKU-1", "name": "Producto 1", "unit_price": "749.99",
         "quantity": 2, "subtotal": "1499.98"},
        {"product_id": 2, "sku": "SKU-2", "name": "Producto 2", "unit_price": "0.10",
         "quantity": 3, "subtotal": "0.30"}]
    assert Decimal(order["total"]) == Decimal("1500.28")


def test_order_keeps_the_prices_of_the_purchase(client, catalog):
    order = checkout(client, fill_cart(client)).json()
    catalog.prices[1] = "999.00"  # the price changes later in product-service

    stored = client.get(f"/orders/{order['id']}", headers=bearer(ME)).json()

    assert stored["items"][0]["unit_price"] == "749.99"
    assert stored["total"] == order["total"]


def test_client_cannot_send_prices(client):
    version = fill_cart(client)

    response = client.post("/orders", json={"cart_version": version, "total": "1.00"},
                           headers={**bearer(ME), "Idempotency-Key": "k"})

    assert response.status_code == 422


# 2-3. Idempotency ------------------------------------------------------------------

def test_retry_with_the_same_key_returns_the_original(client, inventory, catalog):
    version = fill_cart(client)
    first = checkout(client, version, key="buy-1").json()
    calls = (inventory.calls, catalog.calls)

    again = checkout(client, version, key="buy-1")

    assert again.status_code == 200
    assert again.json() == first
    assert (inventory.calls, catalog.calls) == calls  # answered from the stored order only


def test_retry_after_the_order_finished_and_the_cart_was_emptied(client, db_session, catalog):
    version = fill_cart(client)
    first = checkout(client, version, key="buy-1").json()
    # Later the order is paid and the cart emptied (stage 5); simulated here
    db_session.execute(update(Order).where(Order.id == uuid.UUID(first["id"])).values(
        status="PAID", payment_key="p", payment_simulation="APPROVE", payment_status="APPROVED"))
    db_session.commit()
    client.delete("/cart", headers=bearer(ME))
    catalog.down = True  # not needed to answer a retry

    again = checkout(client, version, key="buy-1")

    assert again.status_code == 200
    assert (again.json()["id"], again.json()["status"]) == (first["id"], "PAID")


def test_same_key_with_another_cart_version(client):
    version = fill_cart(client)
    checkout(client, version, key="buy-1")

    response = checkout(client, version + 1, key="buy-1")

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "IDEMPOTENCY_KEY_REUSED"


def test_another_key_while_an_order_is_active(client):
    version = fill_cart(client)
    checkout(client, version, key="buy-1")

    response = checkout(client, version, key="buy-2")

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "CART_LOCKED"


def test_same_key_different_users_are_independent(client):
    a = checkout(client, fill_cart(client, ME), key="same", user=ME)
    b = checkout(client, fill_cart(client, OTHER), key="same", user=OTHER)

    assert (a.status_code, b.status_code) == (201, 201)
    assert a.json()["id"] != b.json()["id"]


def test_idempotency_key_is_required(client):
    version = fill_cart(client)

    response = client.post("/orders", json={"cart_version": version}, headers=bearer(ME))

    assert response.status_code == 422


# 5. Cart checks -------------------------------------------------------------------

def test_old_cart_version_is_rejected(client):
    version = fill_cart(client)

    response = checkout(client, version - 1)

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "CART_CHANGED"


def test_empty_cart(client):
    response = checkout(client, 0)

    assert response.json()["detail"]["code"] == "CART_EMPTY"


# 6. Products ---------------------------------------------------------------------

def test_inactive_product_creates_no_order(client, catalog, db_session):
    version = fill_cart(client)
    del catalog.prices[2]

    response = checkout(client, version)

    assert response.status_code == 409
    assert response.json()["detail"] == {"code": "PRODUCT_NOT_AVAILABLE",
                                         "message": "Some products are no longer available",
                                         "product_ids": [2]}
    assert db_session.scalar(select(Order).where(Order.user_id == ME)) is None


def test_product_service_down_creates_no_order(client, catalog, db_session):
    version = fill_cart(client)
    catalog.down = True

    response = checkout(client, version)

    assert response.status_code == 503
    assert db_session.scalar(select(Order).where(Order.user_id == ME)) is None


# 7. Insufficient stock -----------------------------------------------------------------

def test_insufficient_stock_cancels_and_records_the_release(client, inventory, db_session):
    version = fill_cart(client)
    inventory.mode = "insufficient"

    response = checkout(client, version, key="buy-1")

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert (detail["code"], detail["product_id"]) == ("INSUFFICIENT_STOCK", 1)
    order = client.get(f"/orders/{detail['order_id']}", headers=bearer(ME)).json()
    assert (order["status"], order["cancel_reason"], order["inventory_status"]) == (
        "CANCELLED", "INSUFFICIENT_STOCK", "NOT_RESERVED")
    # No orphan reservation is possible: the release is saved with the cancellation
    # and run right away (inventory leaves a tombstone that rejects a late reservation)
    assert operations(db_session, detail["order_id"]) == [
        ("RELEASE_INVENTORY", f"{detail['order_id']}:RELEASE_INVENTORY", "DONE")]
    assert inventory.status[detail["order_id"]] == "RELEASED"


def test_after_insufficient_stock_the_cart_is_unlocked(client, inventory):
    version = fill_cart(client)
    inventory.mode = "insufficient"
    checkout(client, version)

    assert client.put("/cart/items/1", json={"quantity": 1}, headers=bearer(ME)).status_code == 200


def test_retry_of_a_cancelled_checkout_returns_it(client, inventory):
    version = fill_cart(client)
    inventory.mode = "insufficient"
    first = checkout(client, version, key="buy-1").json()["detail"]
    inventory.mode = "ok"

    again = checkout(client, version, key="buy-1")

    assert again.status_code == 200
    assert (again.json()["id"], again.json()["status"]) == (first["order_id"], "CANCELLED")


# 8-9. inventory-service without answer ---------------------------------------------------------

@pytest.mark.parametrize("mode", ["down", "timeout"])
def test_order_stays_reserving_when_inventory_does_not_answer(client, inventory, mode):
    version = fill_cart(client)
    inventory.mode = mode

    response = checkout(client, version)

    assert response.status_code == 202
    order = response.json()
    assert (order["status"], order["inventory_status"]) == ("RESERVING", "PENDING")
    assert response.headers["Location"] == f"/orders/{order['id']}"
    assert client.get(f"/orders/{order['id']}", headers=bearer(ME)).json()["status"] == "RESERVING"


def test_reserving_order_is_recovered_by_a_retry(client, inventory, db_session):
    version = fill_cart(client)
    inventory.mode = "timeout"        # inventory reserved, but the answer was lost
    first = checkout(client, version, key="buy-1").json()
    inventory.mode = "ok"

    again = checkout(client, version, key="buy-1")

    assert again.status_code == 200
    assert (again.json()["id"], again.json()["status"]) == (first["id"], "PENDING_PAYMENT")
    assert list(inventory.reserved) == [first["id"]]  # the same reservation, reused
    assert db_session.scalar(select(Order).where(Order.user_id == ME, Order.id != uuid.UUID(first["id"]))) is None


def test_retry_while_inventory_is_still_down(client, inventory):
    version = fill_cart(client)
    inventory.mode = "down"
    first = checkout(client, version, key="buy-1").json()

    again = checkout(client, version, key="buy-1")

    assert again.status_code == 202
    assert again.json()["id"] == first["id"]


# 10. Late answers --------------------------------------------------------------------

def test_reservation_already_released_cancels_the_order(client, inventory):
    version = fill_cart(client)
    inventory.mode = "released"

    order = checkout(client, version).json()

    assert (order["status"], order["cancel_reason"], order["inventory_status"]) == (
        "CANCELLED", "RESERVATION_TIMEOUT", "RELEASED")


def test_late_reservation_does_not_reactivate_a_cancelled_order(client, inventory, db_session):
    version = fill_cart(client)
    inventory.mode = "timeout"
    first = checkout(client, version, key="buy-1").json()
    order_id = uuid.UUID(first["id"])
    # Meanwhile the order was cancelled (what the stage 6 worker will do), with its release
    db_session.execute(update(Order).where(Order.id == order_id).values(
        status="CANCELLED", cancel_reason="RESERVATION_TIMEOUT"))
    db_session.add(PendingOperation(order_id=order_id, kind="RELEASE_INVENTORY",
                                    operation_key=f"{order_id}:RELEASE_INVENTORY"))
    db_session.commit()
    inventory.mode = "ok"

    again = checkout(client, version, key="buy-1")

    assert again.json()["status"] == "CANCELLED"  # not reactivated
    assert inventory.reserve_calls == 1  # a cancelled order is not reserved again
    # The release saved with the cancellation frees the late reservation
    assert operations(db_session, first["id"])[0][::2] == ("RELEASE_INVENTORY", "DONE")
    assert inventory.status[first["id"]] == "RELEASED"
    assert again.json()["inventory_status"] == "RELEASED"


# 11. Queries and authorization ---------------------------------------------------------------

def test_customer_sees_only_their_orders(client):
    mine = checkout(client, fill_cart(client, ME), user=ME).json()
    checkout(client, fill_cart(client, OTHER), user=OTHER)

    page = client.get("/orders", headers=bearer(ME)).json()

    assert ([o["id"] for o in page["items"]], page["total"]) == ([mine["id"]], 1)


def test_another_users_order_is_not_found(client):
    other = checkout(client, fill_cart(client, OTHER), user=OTHER).json()

    assert client.get(f"/orders/{other['id']}", headers=bearer(ME)).status_code == 404
    assert client.get(f"/orders/{uuid.uuid4()}", headers=bearer(ME)).status_code == 404


def test_admin_sees_all_orders(client):
    a = checkout(client, fill_cart(client, ME), user=ME).json()
    b = checkout(client, fill_cart(client, OTHER), user=OTHER).json()

    page = client.get("/orders", params={"size": 100}, headers=bearer(1, "admin")).json()

    assert {a["id"], b["id"]} <= {o["id"] for o in page["items"]}
    assert client.get(f"/orders/{a['id']}", headers=bearer(1, "admin")).status_code == 200


def test_orders_require_a_token(client):
    assert client.get("/orders").status_code == 401
    assert client.post("/orders", json={"cart_version": 0}, headers={"Idempotency-Key": "k"}).status_code == 401


def test_pagination_newest_first(client, inventory, db_session):
    ids = []
    for _ in range(3):
        version = fill_cart(client)
        inventory.mode = "insufficient"  # each order ends CANCELLED, so another can start
        ids.append(checkout(client, version).json()["detail"]["order_id"])

    pages = [client.get("/orders", params={"page": p, "size": 2}, headers=bearer(ME)).json()
             for p in (1, 2)]

    assert [len(p["items"]) for p in pages] == [2, 1]
    assert pages[0]["total"] == 3
    assert {o["id"] for p in pages for o in p["items"]} == set(ids)


def test_same_key_seen_as_locked_returns_the_original(client, monkeypatch):
    """Deterministic version of the double-click race: the first lookup of the
    key misses the order that a simultaneous request just created."""
    from app.services import order_service

    version = fill_cart(client)
    first = checkout(client, version, key="double").json()
    real_by_key = order_service._by_key
    calls = {"n": 0}

    def miss_first_lookup(*args):
        calls["n"] += 1
        return None if calls["n"] == 1 else real_by_key(*args)

    monkeypatch.setattr(order_service, "_by_key", miss_first_lookup)

    again = checkout(client, version, key="double")

    assert again.status_code == 200          # not 409 CART_LOCKED
    assert again.json()["id"] == first["id"]
