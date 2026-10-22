"""Shared helpers for the worker tests (real database, own connections)."""
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select, update

from app.database import SessionLocal
from app.models import Cart, Order, PendingOperation
from tests.conftest import bearer


def cleanup(users):
    with SessionLocal() as db:
        db.execute(delete(Order).where(Order.user_id.in_(users)))
        db.execute(delete(Cart).where(Cart.user_id.in_(users)))
        db.commit()


def at_the_same_time(tasks):
    barrier = threading.Barrier(len(tasks))

    def wrapped(task):
        barrier.wait()
        return task()

    with ThreadPoolExecutor(max_workers=len(tasks)) as pool:
        return list(pool.map(wrapped, tasks))


def placed_order(http, user, product=1):
    version = http.put(f"/cart/items/{product}", json={"quantity": 2}, headers=bearer(user)).json()["version"]
    response = http.post("/orders", json={"cart_version": version},
                         headers={**bearer(user), "Idempotency-Key": f"buy-{uuid.uuid4()}"})
    return response.json()


def pay(http, order_id, user, simulation="APPROVE", key="pay-1"):
    return http.post(f"/orders/{order_id}/pay", json={"simulation": simulation},
                     headers={**bearer(user), "Idempotency-Key": key})


def order(order_id):
    with SessionLocal() as db:
        return db.get(Order, uuid.UUID(str(order_id)))


def ops(order_id):
    with SessionLocal() as db:
        rows = db.scalars(select(PendingOperation)
                          .where(PendingOperation.order_id == uuid.UUID(str(order_id)))
                          .order_by(PendingOperation.id)).all()
        return {o.operation_key.split(":", 1)[1]: o for o in rows}


def set_order(order_id, **values):
    with SessionLocal() as db:
        db.execute(update(Order).where(Order.id == uuid.UUID(str(order_id))).values(**values))
        db.commit()


def make_due(order_id, **extra):
    """Moves the next attempt of the order's operations to now (instead of waiting)."""
    with SessionLocal() as db:
        db.execute(update(PendingOperation)
                   .where(PendingOperation.order_id == uuid.UUID(str(order_id)), PendingOperation.status != "DONE")
                   .values(next_attempt_at=datetime.now(UTC) - timedelta(seconds=1), **extra))
        db.commit()
