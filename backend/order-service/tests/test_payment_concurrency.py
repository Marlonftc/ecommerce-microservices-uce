"""9-11, 20. Payment races and the executor lease, against the real database
(own connections). Everything created is deleted at the end."""
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, update

from app.database import SessionLocal, engine
from app.main import app
from app.models import Cart, Order, PendingOperation
from app.services import executor
from tests.conftest import bearer

USERS = [900601, 900602, 900603, 900604, 900605]


@pytest.fixture
def http(catalog, inventory, payments, notifications):
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


def placed_order(http, user):
    version = http.put("/cart/items/1", json={"quantity": 2}, headers=bearer(user)).json()["version"]
    return http.post("/orders", json={"cart_version": version},
                     headers={**bearer(user), "Idempotency-Key": f"buy-{uuid.uuid4()}"}).json()


def pay(http, order_id, user, key="pay-1"):
    return http.post(f"/orders/{order_id}/pay", json={"simulation": "APPROVE"},
                     headers={**bearer(user), "Idempotency-Key": key})


def resolve_op(order_id):
    with SessionLocal() as db:
        return db.scalar(select(PendingOperation.id).where(
            PendingOperation.order_id == uuid.UUID(order_id), PendingOperation.kind == "RESOLVE_PAYMENT"))


def test_same_payment_ten_times_at_once(http, payments):
    user = USERS[0]
    order = placed_order(http, user)
    version_before = http.get("/cart", headers=bearer(user)).json()["version"]

    responses = at_the_same_time(lambda i: pay(http, order["id"], user), 10)

    assert {r.status_code for r in responses} <= {200, 202}
    assert len(payments.payments) == 1
    final = http.get(f"/orders/{order['id']}", headers=bearer(user)).json()
    assert final["status"] == "PAID"
    # the cart was emptied exactly once
    assert http.get("/cart", headers=bearer(user)).json()["version"] == version_before + 1


def test_ten_different_keys_at_once_one_payment(http, payments):
    user = USERS[1]
    order = placed_order(http, user)

    responses = at_the_same_time(lambda i: pay(http, order["id"], user, key=f"pay-{i}"), 10)

    codes = [r.status_code for r in responses]
    assert codes.count(409) == 9
    assert all(r.json()["detail"]["code"] == "IDEMPOTENCY_KEY_REUSED" for r in responses if r.status_code == 409)
    assert len(payments.payments) == 1


def test_ten_executors_claim_the_same_operation(http, payments):
    user = USERS[2]
    order = placed_order(http, user)
    payments.mode = "down"
    pay(http, order["id"], user)                 # RESOLVE_PAYMENT stays pending
    op_id = resolve_op(order["id"])
    payments.mode = "ok"
    requests_before = len(payments.requests)

    def run(_):
        with SessionLocal() as db:
            return executor.run_operation(db, op_id, ignore_schedule=True)

    results = at_the_same_time(run, 10)

    assert sorted(results) == ["DONE"] + ["SKIPPED"] * 9
    assert len(payments.requests) == requests_before + 1


def test_expired_lease_cannot_overwrite_the_next_runner(http, payments):
    user = USERS[3]
    order = placed_order(http, user)
    payments.mode = "down"
    pay(http, order["id"], user)
    op_id = resolve_op(order["id"])
    payments.mode = "ok"

    with SessionLocal() as db:
        first = executor.claim(db, op_id, ignore_schedule=True)
        # the first runner stalls longer than its lease
        db.execute(update(PendingOperation).where(PendingOperation.id == op_id)
                   .values(locked_until=datetime.now(UTC) - timedelta(seconds=1)))
        db.commit()
        second = executor.claim(db, op_id, ignore_schedule=True)
        assert second is not None and second.token != first.token

        # the stale runner comes back: it can neither finish nor record an error
        assert executor._finish(db, first, lambda db: None) == "LOST"
        assert executor._fail(db, first, RuntimeError("late"), permanent=True) == "LOST"
        assert executor._resolve_payment(db, second) == "DONE"

        op = db.get(PendingOperation, op_id)
        assert (op.status, op.error_kind, op.last_error) == ("DONE", None, None)


def test_pending_operation_survives_closing_every_connection(http, payments):
    """The operation lives in PostgreSQL, not in the memory of the process:
    after closing all connections a new executor finishes it."""
    user = USERS[4]
    order = placed_order(http, user)
    payments.mode = "down"
    pay(http, order["id"], user)
    op_id = resolve_op(order["id"])

    engine.dispose()            # every pooled connection is closed
    payments.mode = "ok"
    with SessionLocal() as db:
        stored = db.get(PendingOperation, op_id)
        assert (stored.status, stored.payload["amount"]) == ("PENDING", "1499.98")
        assert executor.run_operation(db, op_id, ignore_schedule=True) == "DONE"
        executor.run_order_operations(db, uuid.UUID(order["id"]), ignore_schedule=True)
        assert db.get(Order, uuid.UUID(order["id"])).status == "PAID"
