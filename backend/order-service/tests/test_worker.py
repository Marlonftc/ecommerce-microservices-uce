"""Stage 6: worker cycle, expiration, RESERVING sweep, cancellation, retries
and leases. Real PostgreSQL with own connections; the other services are
SIMULATED here (the real integration is stage 7). Test users: 900701...
"""
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from app.config import Settings, settings
from app.database import SessionLocal, engine
from app.main import app
from app.models import PendingOperation
from app.services import executor, recovery
from app.worker import Worker
from tests.conftest import bearer
from tests.worker_helpers import (at_the_same_time, cleanup, make_due, ops, order, pay,
                                  placed_order, set_order)

U = list(range(900701, 900731))
PAST = datetime.now(UTC) - timedelta(minutes=1)


@pytest.fixture
def http(catalog, inventory, payments, notifications):
    try:
        engine.connect().close()
    except Exception:
        pytest.skip("database not available")
    app.dependency_overrides.clear()
    cleanup(U)
    yield TestClient(app)
    cleanup(U)


def run_worker():
    return Worker().run_once()


# 1. SLOW_APPROVE reconciled without the customer ----------------------------------------

def test_worker_resolves_slow_approve_automatically(http, payments, notifications):
    o = placed_order(http, U[0])
    assert pay(http, o["id"], U[0], "SLOW_APPROVE").status_code == 202
    make_due(o["id"])  # instead of waiting for the backoff

    stats = run_worker()

    assert stats["done"] >= 3  # payment, confirmation, notification
    final = order(o["id"])
    assert (final.status, final.inventory_status) == ("PAID", "CONFIRMED")
    assert len(payments.payments) == 1 and payments.requests[0] == payments.requests[1]
    assert f"{o['id']}:ORDER_PAID" in notifications.events


# 2, 16. Expiration ----------------------------------------------------------------------

def test_expired_order_is_cancelled_released_and_notified(http, inventory, notifications):
    o = placed_order(http, U[1])
    set_order(o["id"], expires_at=PAST)

    stats = run_worker()

    assert stats["expired"] == 1
    final = order(o["id"])
    assert (final.status, final.cancel_reason, final.inventory_status) == ("CANCELLED", "EXPIRED", "RELEASED")
    assert inventory.status[o["id"]] == "RELEASED"
    assert notifications.events[f"{o['id']}:ORDER_CANCELLED"]["type"] == "ORDER_CANCELLED"
    assert http.get("/cart", headers=bearer(U[1])).json()["locked"] is False


def test_only_due_pending_payment_orders_expire(http, payments):
    waiting = placed_order(http, U[2])                       # still has time
    paying = placed_order(http, U[3])
    payments.mode = "down"
    pay(http, paying["id"], U[3])                            # PAYMENT_PENDING
    set_order(paying["id"], expires_at=PAST)                 # its time ran out meanwhile

    run_worker()

    assert order(waiting["id"]).status == "PENDING_PAYMENT"
    assert order(paying["id"]).status == "PAYMENT_PENDING"   # unknown payment: never expires


# 3. Payment and expiration at the same time ------------------------------------------------

def test_payment_and_expiration_never_both_win(http, payments):
    results = []
    for user in U[4:14]:
        o = placed_order(http, user)
        set_order(o["id"], expires_at=datetime.now(UTC) + timedelta(milliseconds=40))

        def pay_it(o=o, user=user):
            return pay(http, o["id"], user).status_code

        def expire_it():
            import time
            time.sleep(0.04)
            with SessionLocal() as db:
                return recovery.expire_orders(db)

        at_the_same_time([pay_it, expire_it])
        final, operations = order(o["id"]), ops(o["id"])
        if final.status == "CANCELLED":
            assert "RESOLVE_PAYMENT" not in operations and final.payment_key is None
            assert o["id"] not in payments.payments
        else:
            assert final.status == "PAID" and "ORDER_CANCELLED" not in operations
        results.append(final.status)
    assert set(results) <= {"PAID", "CANCELLED"}


# 4, 16. Cancellation by the customer --------------------------------------------------------

def cancel(http, order_id, user):
    return http.post(f"/orders/{order_id}/cancel", headers=bearer(user))


def test_customer_cancels(http, inventory, notifications):
    o = placed_order(http, U[14])

    response = cancel(http, o["id"], U[14])

    assert response.status_code == 200
    body = response.json()
    assert (body["status"], body["cancel_reason"], body["inventory_status"]) == ("CANCELLED", "USER", "RELEASED")
    assert notifications.events[f"{o['id']}:ORDER_CANCELLED"]["amount"] == o["total"]
    assert http.put("/cart/items/2", json={"quantity": 1}, headers=bearer(U[14])).status_code == 200


def test_repeated_cancellation_adds_nothing(http, notifications):
    o = placed_order(http, U[15])
    cancel(http, o["id"], U[15])
    calls = notifications.calls

    again = cancel(http, o["id"], U[15])

    assert again.status_code == 200
    assert sorted(ops(o["id"])) == ["ORDER_CANCELLED", "RELEASE_INVENTORY"]
    assert notifications.calls == calls


def test_orders_that_cannot_be_cancelled(http):
    paid = placed_order(http, U[16])
    pay(http, paid["id"], U[16])

    response = cancel(http, paid["id"], U[16])

    assert response.status_code == 409
    assert response.json()["detail"]["status"] == "PAID"


def test_only_the_owner_cancels(http):
    o = placed_order(http, U[17])

    assert cancel(http, o["id"], U[18]).status_code == 404
    assert cancel(http, o["id"], 1).status_code == 404  # an admin is not the owner either
    assert http.post(f"/orders/{o['id']}/cancel").status_code == 401
    assert order(o["id"]).status == "PENDING_PAYMENT"


# 5. Payment and cancellation at the same time ---------------------------------------------------

def test_payment_and_cancellation_never_both_win(http, payments):
    for user in U[19:24]:
        o = placed_order(http, user)

        paid, cancelled = at_the_same_time([lambda: pay(http, o["id"], user),
                                            lambda: cancel(http, o["id"], user)])

        final = order(o["id"])
        assert sorted([paid.status_code, cancelled.status_code]) == [200, 409]
        if final.status == "CANCELLED":
            assert paid.json()["detail"]["code"] == "ORDER_NOT_PAYABLE"
            assert o["id"] not in payments.payments
        else:
            assert final.status == "PAID"
            assert cancelled.json()["detail"]["code"] == "ORDER_NOT_CANCELLABLE"


# 6, 7, 15. RESERVING sweep --------------------------------------------------------------------

def test_old_reserving_order_is_swept_without_notification(http, inventory, notifications):
    inventory.mode = "down"
    old = placed_order(http, U[24])
    set_order(old["id"], created_at=datetime.now(UTC) - timedelta(minutes=settings.reserving_sweep_minutes + 1))
    recent = placed_order(http, U[25])
    inventory.mode = "ok"

    stats = run_worker()

    assert stats["swept"] == 1
    final = order(old["id"])
    assert (final.status, final.cancel_reason, final.inventory_status) == (
        "CANCELLED", "RESERVATION_TIMEOUT", "RELEASED")
    assert inventory.status[old["id"]] == "RELEASED"            # tombstone: a late reservation is rejected
    assert not [e for e in notifications.events if e.startswith(old["id"])]
    assert order(recent["id"]).status == "RESERVING"            # not old enough


def test_late_reservation_is_freed_and_does_not_reactivate(http, inventory):
    inventory.mode = "timeout"                 # inventory reserved, the answer was lost
    o = placed_order(http, U[26])
    assert inventory.status[o["id"]] == "RESERVED"
    set_order(o["id"], created_at=datetime.now(UTC) - timedelta(minutes=10))
    inventory.mode = "ok"

    run_worker()

    assert inventory.status[o["id"]] == "RELEASED"             # no stock kept by a cancelled order
    assert order(o["id"]).status == "CANCELLED"


def test_inventory_status_is_released_only_after_inventory_confirms(http, inventory):
    inventory.mode = "down"
    o = placed_order(http, U[27])
    set_order(o["id"], created_at=datetime.now(UTC) - timedelta(minutes=10))
    inventory.release_mode = "down"

    run_worker()

    final = order(o["id"])
    assert (final.status, final.inventory_status) == ("CANCELLED", "PENDING")  # not RELEASED yet
    assert ops(o["id"])["RELEASE_INVENTORY"].status == "PENDING"


# 8, 10. Worker crash and expired lease -------------------------------------------------------------

def test_operation_of_a_crashed_worker_is_recovered(http, notifications):
    o = placed_order(http, U[28])
    notifications.mode = "down"
    pay(http, o["id"], U[28])
    op_id = ops(o["id"])["ORDER_PAID"].id
    notifications.mode = "ok"
    make_due(o["id"])
    with SessionLocal() as db:
        crashed = executor.claim(db, op_id)   # a worker claims it and dies
    assert crashed is not None

    assert run_worker()["done"] == 0          # the lease is still valid: nobody touches it

    with SessionLocal() as db:
        db.execute(update(PendingOperation).where(PendingOperation.id == op_id)
                   .values(locked_until=datetime.now(UTC) - timedelta(seconds=1)))
        db.commit()
    assert run_worker()["done"] == 1          # expired lease: another worker finishes it
    assert ops(o["id"])["ORDER_PAID"].status == "DONE"


# 9. Two workers on the same queue ------------------------------------------------------------------

def test_two_workers_never_take_the_same_operation(http, notifications):
    notifications.mode = "down"
    paid = []
    for _ in range(8):  # 8 paid orders, each with a pending ORDER_PAID notification
        o = placed_order(http, U[29])
        pay(http, o["id"], U[29], key=f"pay-{uuid.uuid4()}")
        make_due(o["id"])
        paid.append(o["id"])
    notifications.mode = "ok"
    calls = notifications.calls

    first, second = at_the_same_time([run_worker, run_worker])

    assert first["done"] + second["done"] == 8
    assert notifications.calls - calls == 8        # one call per operation
    assert all(ops(i)["ORDER_PAID"].status == "DONE" for i in paid)


# 11-12. Retries, slow lane and permanent failures ---------------------------------------------------

def test_backoff_then_slow_lane_then_success(http, payments):
    o = placed_order(http, U[0])
    payments.mode = "down"
    pay(http, o["id"], U[0])
    op = ops(o["id"])["RESOLVE_PAYMENT"]
    assert op.next_attempt_at > datetime.now(UTC)
    assert run_worker()["retry"] == 0                       # not due yet: the worker waits

    make_due(o["id"])
    run_worker()
    assert ops(o["id"])["RESOLVE_PAYMENT"].attempts == 2    # normal retry

    make_due(o["id"], attempts=settings.worker_max_fast_attempts - 1)
    run_worker()
    op = ops(o["id"])["RESOLVE_PAYMENT"]
    assert (op.status, op.error_kind) == ("FAILED", "TRANSIENT")   # slow lane, not dropped
    assert run_worker()["retry"] == 0                       # waits 15 minutes

    payments.mode = "ok"
    make_due(o["id"])
    run_worker()
    assert ops(o["id"])["RESOLVE_PAYMENT"].status == "DONE"
    assert order(o["id"]).status == "PAID"


def test_permanent_failures_never_run_automatically(http, payments):
    o = placed_order(http, U[1])
    payments.mode = "conflict"
    pay(http, o["id"], U[1])
    assert ops(o["id"])["RESOLVE_PAYMENT"].error_kind == "PERMANENT"
    payments.mode = "ok"
    make_due(o["id"])
    requests = len(payments.requests)

    run_worker()

    assert len(payments.requests) == requests
    assert order(o["id"]).status == "PAYMENT_PENDING"


def test_lease_is_long_enough_for_the_slowest_call():
    assert settings.worker_lease_seconds >= 2 * (2 * settings.http_connect_timeout_seconds
                                                 + settings.http_read_timeout_seconds)
    with pytest.raises(ValueError, match="WORKER_LEASE_SECONDS"):
        Settings(_env_file=None, db_password="x", internal_api_key="k", worker_lease_seconds=10)


# 18. Persistence ---------------------------------------------------------------------------------

def test_new_worker_resumes_after_all_connections_closed(http, notifications):
    notifications.mode = "down"
    o = placed_order(http, U[2])
    pay(http, o["id"], U[2])
    make_due(o["id"])
    engine.dispose()
    notifications.mode = "ok"

    assert run_worker()["done"] == 1
    assert ops(o["id"])["ORDER_PAID"].status == "DONE"


# 19. Stop request inside a cycle --------------------------------------------------------------------

def test_stop_request_claims_nothing_more(http, notifications, monkeypatch):
    notifications.mode = "down"
    ids = []
    for _ in range(3):
        o = placed_order(http, U[3])
        pay(http, o["id"], U[3], key=f"pay-{uuid.uuid4()}")
        make_due(o["id"])
        ids.append(o["id"])
    notifications.mode = "ok"
    worker = Worker()
    real_execute = executor.execute

    def execute_then_stop(db, claimed):
        result = real_execute(db, claimed)
        worker.request_stop()   # SIGTERM arrives during the first operation
        return result
    monkeypatch.setattr(executor, "execute", execute_then_stop)

    stats = worker.run_once()

    assert stats["done"] == 1
    remaining = [ops(i)["ORDER_PAID"] for i in ids if ops(i)["ORDER_PAID"].status != "DONE"]
    assert len(remaining) == 2
    assert all(op.lease_token is None for op in remaining)  # never claimed: free for the next worker


def test_a_locked_operation_does_not_block_the_worker(http, notifications):
    """SKIP LOCKED: a row locked by another transaction is skipped, not waited for.
    (Safety against duplicates comes from the lease; this is about not blocking.)"""
    import threading

    notifications.mode = "down"
    ids = []
    for _ in range(2):
        o = placed_order(http, U[4])
        pay(http, o["id"], U[4], key=f"pay-{uuid.uuid4()}")
        make_due(o["id"])
        ids.append(o["id"])
    notifications.mode = "ok"
    first_op = ops(ids[0])["ORDER_PAID"].id

    holder = SessionLocal()
    holder.execute(select(PendingOperation).where(PendingOperation.id == first_op).with_for_update())
    result = {}
    thread = threading.Thread(target=lambda: result.update(run_worker()), daemon=True)
    try:
        thread.start()
        thread.join(timeout=5)
        finished_while_locked = not thread.is_alive()
    finally:
        holder.rollback()
        holder.close()
        thread.join(timeout=10)

    assert finished_while_locked                       # did not wait for the locked row
    assert ops(ids[1])["ORDER_PAID"].status == "DONE"  # took the next one instead
