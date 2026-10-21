"""Simultaneous HTTP requests against the real database.

No rollback session here: each request opens its own connection, as in
production. Everything created is deleted at the end.
"""
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.database import SessionLocal, engine
from app.main import app
from app.models import Notification
from tests.conftest import INTERNAL, bearer, event_body

http = TestClient(app)


@pytest.fixture
def orders():
    try:
        engine.connect().close()
    except Exception:
        pytest.skip("database not available")
    created: list[uuid.UUID] = []
    yield created
    with SessionLocal() as db:
        db.execute(delete(Notification).where(Notification.order_id.in_(created)))
        db.commit()


def at_the_same_time(task, count):
    barrier = threading.Barrier(count)

    def wrapped(index):
        barrier.wait()  # all threads start together
        return task(index)

    with ThreadPoolExecutor(max_workers=count) as pool:
        return list(pool.map(wrapped, range(count)))


def stored(order_id):
    with SessionLocal() as db:
        return list(db.scalars(select(Notification).where(Notification.order_id == order_id)))


def test_same_event_ten_times_at_once_creates_one(orders):
    order_id = uuid.uuid4()
    orders.append(order_id)
    body = event_body(order_id=order_id)

    responses = at_the_same_time(
        lambda i: http.post("/internal/notifications", json=body, headers=INTERNAL), 10)

    codes = sorted(r.status_code for r in responses)
    assert codes == [200] * 9 + [201]
    assert len({r.json()["id"] for r in responses}) == 1  # everybody got the same one
    assert len(stored(order_id)) == 1


def test_same_event_id_with_different_data_at_once(orders):
    order_id = uuid.uuid4()
    orders.append(order_id)

    # Ten different users for the same event_id: only one can win
    responses = at_the_same_time(
        lambda i: http.post("/internal/notifications",
                            json=event_body(order_id=order_id, user_id=100 + i), headers=INTERNAL), 10)

    codes = sorted(r.status_code for r in responses)
    assert codes == [201] + [409] * 9  # no 500
    winner = next(r.json() for r in responses if r.status_code == 201)
    assert [n.user_id for n in stored(order_id)] == [winner["user_id"]]


def test_marking_as_read_at_once_sets_one_read_date(orders):
    order_id = uuid.uuid4()
    orders.append(order_id)
    nid = http.post("/internal/notifications", json=event_body(order_id=order_id, user_id=7),
                    headers=INTERNAL).json()["id"]

    responses = at_the_same_time(lambda i: http.patch(f"/notifications/{nid}/read", headers=bearer(7)), 10)

    assert {r.status_code for r in responses} == {200}
    assert len({r.json()["read_at"] for r in responses}) == 1
    assert stored(order_id)[0].read_at is not None
