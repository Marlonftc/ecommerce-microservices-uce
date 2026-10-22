"""Admin endpoints for operations. Real PostgreSQL, simulated services."""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.database import engine
from app.main import app
from app.services import executor
from app.database import SessionLocal
from tests.conftest import bearer
from tests.worker_helpers import at_the_same_time, cleanup, ops, order, pay, placed_order

U = [900801, 900802, 900803]
ADMIN = bearer(1, "admin")


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


def failed_payment(http, payments, user):
    o = placed_order(http, user)
    payments.mode = "conflict"
    pay(http, o["id"], user)
    payments.mode = "ok"
    return o, ops(o["id"])["RESOLVE_PAYMENT"]


def test_admin_lists_failed_operations(http, payments):
    o, op = failed_payment(http, payments, U[0])

    page = http.get("/admin/operations", params={"status": "FAILED", "order_id": o["id"]},
                    headers=ADMIN).json()

    assert page["total"] == 1
    item = page["items"][0]
    assert (item["id"], item["kind"], item["error_kind"]) == (op.id, "RESOLVE_PAYMENT", "PERMANENT")
    assert "idempotency conflict" in item["last_error"]
    assert set(item["payload"]) == {"user_id", "amount", "currency", "simulation"}  # no secrets


def test_admin_reactivation_runs_the_operation(http, payments):
    o, op = failed_payment(http, payments, U[1])  # the cause is "fixed" (payments back to normal)

    response = http.post(f"/admin/operations/{op.id}/retry", headers=ADMIN)

    assert response.status_code == 200
    body = response.json()
    assert (body["outcome"], body["operation"]["status"], body["operation"]["attempts"]) == ("DONE", "DONE", 1)
    executor_order = order(o["id"])
    assert executor_order.status == "PAID"


def test_only_failed_and_unclaimed_operations_can_be_reactivated(http, payments):
    o = placed_order(http, U[2])
    pay(http, o["id"], U[2])
    done = ops(o["id"])["RESOLVE_PAYMENT"]

    response = http.post(f"/admin/operations/{done.id}/retry", headers=ADMIN)

    assert response.status_code == 409
    assert response.json()["detail"]["status"] == "DONE"
    assert http.post("/admin/operations/999999999/retry", headers=ADMIN).status_code == 404


def test_claimed_failed_operation_cannot_be_reactivated(http, payments):
    o, op = failed_payment(http, payments, U[0])
    with SessionLocal() as db:  # a FAILED/PERMANENT op cannot be claimed; simulate a lease directly
        from sqlalchemy import update
        from datetime import UTC, datetime, timedelta
        from app.models import PendingOperation
        db.execute(update(PendingOperation).where(PendingOperation.id == op.id)
                   .values(lease_token=uuid.uuid4(), locked_until=datetime.now(UTC) + timedelta(seconds=60)))
        db.commit()

    response = http.post(f"/admin/operations/{op.id}/retry", headers=ADMIN)

    assert response.status_code == 409
    assert response.json()["detail"]["claimed"] is True


def test_concurrent_reactivation_only_one_wins(http, payments):
    o, op = failed_payment(http, payments, U[1])
    requests = len(payments.requests)

    responses = at_the_same_time([lambda: http.post(f"/admin/operations/{op.id}/retry", headers=ADMIN)
                                  for _ in range(10)])

    codes = sorted(r.status_code for r in responses)
    assert codes == [200] + [409] * 9
    assert len(payments.requests) == requests + 1


def test_admin_endpoints_require_the_admin_role(http):
    assert http.get("/admin/operations", headers=bearer(U[0])).status_code == 403
    assert http.post("/admin/operations/1/retry", headers=bearer(U[0])).status_code == 403
    assert http.get("/admin/operations").status_code == 401
