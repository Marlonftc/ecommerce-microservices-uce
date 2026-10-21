"""4-5. Simultaneous checkouts against the real database (own connections).

Everything created is deleted at the end.
"""
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

from app.database import SessionLocal, engine
from app.main import app
from app.models import Cart, Order
from app.services import cart_service
from tests.conftest import bearer

USERS = [900401, 900402, 900403]


@pytest.fixture
def http(catalog, inventory):
    try:
        engine.connect().close()
    except Exception:
        pytest.skip("database not available")
    app.dependency_overrides.clear()
    yield TestClient(app)
    with SessionLocal() as db:
        db.execute(delete(Order).where(Order.user_id.in_(USERS)))
        db.execute(delete(Cart).where(Cart.user_id.in_(USERS)))
        db.commit()


def at_the_same_time(task, count):
    barrier = threading.Barrier(count)

    def wrapped(index):
        barrier.wait()
        return task(index)

    with ThreadPoolExecutor(max_workers=count) as pool:
        return list(pool.map(wrapped, range(count)))


def fill(http, user):
    http.put("/cart/items/1", json={"quantity": 1}, headers=bearer(user))
    return http.put("/cart/items/2", json={"quantity": 2}, headers=bearer(user)).json()["version"]


def orders_of(user):
    with SessionLocal() as db:
        return db.scalar(select(func.count()).select_from(Order).where(Order.user_id == user))


def post(http, user, version, key):
    return http.post("/orders", json={"cart_version": version},
                     headers={**bearer(user), "Idempotency-Key": key})


def test_same_key_ten_times_at_once_creates_one_order(http, inventory):
    user = USERS[0]
    version = fill(http, user)

    responses = at_the_same_time(lambda i: post(http, user, version, "double-click"), 10)

    codes = sorted(r.status_code for r in responses)
    assert codes == [200] * 9 + [201]
    assert len({r.json()["id"] for r in responses}) == 1
    assert orders_of(user) == 1
    assert len(inventory.reserved) == 1


def test_ten_different_keys_at_once_only_one_checkout(http):
    user = USERS[1]
    version = fill(http, user)

    responses = at_the_same_time(lambda i: post(http, user, version, f"key-{i}"), 10)

    codes = sorted(r.status_code for r in responses)
    assert codes == [201] + [409] * 9
    assert all(r.json()["detail"]["code"] == "CART_LOCKED" for r in responses if r.status_code == 409)
    assert orders_of(user) == 1


def test_cart_changed_while_prices_were_read(http, catalog):
    user = USERS[2]
    version = fill(http, user)

    def change_cart_once():
        # Another request of the same user changes the cart, with its own connection,
        # between the first read and the lock of the checkout
        catalog.on_call = None
        with SessionLocal() as db:
            cart_service.set_quantity(db, user, 1, 5)

    catalog.on_call = change_cart_once
    response = post(http, user, version, f"key-{uuid.uuid4()}")

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "CART_CHANGED"
    assert orders_of(user) == 0
