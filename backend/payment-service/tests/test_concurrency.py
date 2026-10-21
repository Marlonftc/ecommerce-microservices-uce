"""Simultaneous requests against the real database.

Each thread uses its own connection, like real requests. Everything created
here is deleted at the end.
"""
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, text

from app.config import settings
from app.database import SessionLocal, engine
from app.main import app
from app.models import Payment
from app.schemas import PaymentIn
from app.services import payment_service as ps
from tests.conftest import INTERNAL, payment_body


@pytest.fixture
def real_db():
    try:
        engine.connect().close()
    except Exception:
        pytest.skip("database not available")
    orders: list[uuid.UUID] = []
    yield orders
    with SessionLocal() as db:
        db.execute(delete(Payment).where(Payment.order_id.in_(orders)))
        db.commit()


def run_at_the_same_time(task, count):
    barrier = threading.Barrier(count)

    def wrapped(index):
        barrier.wait()
        return task(index)

    with ThreadPoolExecutor(max_workers=count) as pool:
        return list(pool.map(wrapped, range(count)))


def attempt(data, key):
    with SessionLocal() as db:
        try:
            payment, created = ps.process_payment(db, data, key)
            return ("created" if created else "replayed", payment.id)
        except (ps.OrderAlreadyHasPayment, ps.IdempotencyKeyReused):
            return ("conflict", None)


def count_payments(order_id):
    with SessionLocal() as db:
        return len(db.scalars(select(Payment).where(Payment.order_id == order_id)).all())


def test_same_request_ten_times_at_once_creates_one_payment(real_db):
    data = PaymentIn(**payment_body())
    real_db.append(data.order_id)

    results = run_at_the_same_time(lambda i: attempt(data, "same-key-" + str(data.order_id)), 10)

    assert [r[0] for r in results].count("created") == 1
    assert len({r[1] for r in results}) == 1  # everybody got the same payment
    assert count_payments(data.order_id) == 1


def test_same_order_with_ten_different_keys_creates_one_payment(real_db):
    data = PaymentIn(**payment_body())
    real_db.append(data.order_id)

    results = run_at_the_same_time(lambda i: attempt(data, f"key-{i}-{data.order_id}"), 10)

    assert [r[0] for r in results].count("created") == 1
    assert [r[0] for r in results].count("conflict") == 9
    assert count_payments(data.order_id) == 1


def test_slow_payment_is_saved_before_the_delay(real_db, monkeypatch):
    monkeypatch.setattr(settings, "payment_slow_delay_seconds", 2)
    body = payment_body(simulation="SLOW_APPROVE")
    real_db.append(uuid.UUID(body["order_id"]))
    client = TestClient(app)

    with ThreadPoolExecutor(max_workers=1) as pool:
        started = time.monotonic()
        future = pool.submit(client.post, "/internal/payments", json=body,
                             headers={**INTERNAL, "Idempotency-Key": "slow-" + body["order_id"]})
        time.sleep(0.8)

        # The HTTP answer has not arrived yet, but the result is already in PostgreSQL
        assert not future.done()
        with SessionLocal() as db:
            stored = db.scalar(select(Payment).where(Payment.order_id == uuid.UUID(body["order_id"])))
        assert stored.status == "APPROVED"

        response = future.result()
    assert response.status_code == 201
    assert time.monotonic() - started >= 2

    # A repeated request (reconciliation) answers at once, without waiting again
    started = time.monotonic()
    again = client.get("/internal/payments", params={"order_id": body["order_id"]}, headers=INTERNAL)
    assert again.json()["status"] == "APPROVED"
    assert time.monotonic() - started < 1


def test_slow_payment_holds_no_database_connection_while_waiting(real_db, monkeypatch):
    monkeypatch.setattr(settings, "payment_slow_delay_seconds", 2)
    body = payment_body(simulation="SLOW_APPROVE")
    real_db.append(uuid.UUID(body["order_id"]))
    client = TestClient(app)

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(client.post, "/internal/payments", json=body,
                             headers={**INTERNAL, "Idempotency-Key": "slow2-" + body["order_id"]})
        time.sleep(0.8)
        with engine.connect() as conn:
            open_transactions = conn.execute(
                text("SELECT count(*) FROM pg_stat_activity "
                     "WHERE datname = current_database() AND state LIKE :pattern"),
                {"pattern": "idle in transaction%"},
            ).scalar()
        future.result()

    assert open_transactions == 0
