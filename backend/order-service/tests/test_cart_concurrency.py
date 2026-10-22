"""14. Simultaneous changes of the same cart against the real database.

No rollback session: each request opens its own connection, as in
production. Everything created is deleted at the end.
"""
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.database import SessionLocal, engine
from app.main import app
from app.models import Cart, CartItem
from tests.conftest import bearer

USERS = [900201, 900202, 900203]


@pytest.fixture
def http(catalog):
    try:
        engine.connect().close()
    except Exception:
        pytest.skip("database not available")
    app.dependency_overrides.clear()
    yield TestClient(app)
    with SessionLocal() as db:
        db.execute(delete(Cart).where(Cart.user_id.in_(USERS)))
        db.commit()


def at_the_same_time(task, count):
    barrier = threading.Barrier(count)

    def wrapped(index):
        barrier.wait()
        return task(index)

    with ThreadPoolExecutor(max_workers=count) as pool:
        return list(pool.map(wrapped, range(count)))


def existing_cart(user_id):
    # The cart must exist before the race: otherwise the simultaneous INSERTs of
    # the cart row wait for each other and hide a missing FOR UPDATE
    with SessionLocal() as db:
        db.add(Cart(user_id=user_id))
        db.commit()
    return user_id


def stored(user_id):
    with SessionLocal() as db:
        version = db.scalar(select(Cart.version).where(Cart.user_id == user_id))
        items = dict(db.execute(select(CartItem.product_id, CartItem.quantity)
                                .where(CartItem.user_id == user_id)).all())
        return version, items


def test_ten_products_added_at_once_none_lost(http):
    user = existing_cart(USERS[0])

    responses = at_the_same_time(
        lambda i: http.put(f"/cart/items/{i + 1}", json={"quantity": 1}, headers=bearer(user)), 10)

    assert {r.status_code for r in responses} == {200}
    version, items = stored(user)
    assert set(items) == set(range(1, 11))   # no lost update
    assert version == 10                      # exactly one increment per change


def test_ten_changes_with_the_same_version_only_one_wins(http):
    user = existing_cart(USERS[1])

    responses = at_the_same_time(
        lambda i: http.put(f"/cart/items/{i + 1}", json={"quantity": 1, "expected_version": 0},
                           headers=bearer(user)), 10)

    codes = sorted(r.status_code for r in responses)
    assert codes == [200] + [409] * 9
    assert all(r.json()["detail"]["code"] == "CART_CHANGED" for r in responses if r.status_code == 409)
    version, items = stored(user)
    assert (version, len(items)) == (1, 1)


def test_same_quantity_ten_times_at_once_changes_once(http):
    user = existing_cart(USERS[2])

    responses = at_the_same_time(
        lambda i: http.put("/cart/items/1", json={"quantity": 4}, headers=bearer(user)), 10)

    assert {r.status_code for r in responses} == {200}
    assert stored(user) == (1, {1: 4})
