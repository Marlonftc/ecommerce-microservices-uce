import uuid

from app.config import settings
from app.models import Payment
from sqlalchemy import func, select
from tests.conftest import INTERNAL, bearer, pay, payment_body


# --- protection -------------------------------------------------------------

def test_internal_endpoints_require_the_internal_key(client):
    assert pay(client, headers={}).status_code == 403
    assert pay(client, headers={"X-Internal-Key": "wrong"}).status_code == 403
    assert pay(client, headers=bearer(1, "admin")).status_code == 403


def test_idempotency_key_is_required(client):
    response = client.post("/internal/payments", json=payment_body(), headers=INTERNAL)

    assert response.status_code == 422


# --- the three simulation modes ---------------------------------------------

def test_approved_payment(client):
    response = pay(client, payment_body(simulation="APPROVE"))

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "APPROVED"
    assert body["amount"] == "149.90" and body["currency"] == "USD"
    assert body["processed_at"] is not None and body["failure_reason"] is None


def test_rejected_payment(client):
    body = pay(client, payment_body(simulation="REJECT")).json()

    assert body["status"] == "REJECTED"
    assert body["failure_reason"] == "simulated rejection"


def test_slow_payment_is_approved(client, monkeypatch):
    monkeypatch.setattr(settings, "payment_slow_delay_seconds", 0.1)

    body = pay(client, payment_body(simulation="SLOW_APPROVE")).json()

    assert body["status"] == "APPROVED"


# --- amount validation ------------------------------------------------------

def test_invalid_amounts_are_rejected_before_the_database(client, db_session):
    for amount in ("0", "-10.00", "10.999", "1234567890123.00"):
        assert pay(client, payment_body(amount=amount)).status_code == 422

    assert db_session.scalar(select(func.count()).select_from(Payment)) == 0


def test_only_usd_is_accepted(client):
    assert pay(client, payment_body(currency="EUR")).status_code == 422


def test_card_data_is_not_accepted(client):
    assert pay(client, payment_body(card_number="4111111111111111")).status_code == 422


# --- idempotency ------------------------------------------------------------

def test_same_key_and_data_returns_the_original_payment(client, db_session):
    body = payment_body()
    first = pay(client, body, key="k-1")
    second = pay(client, body, key="k-1")

    assert (first.status_code, second.status_code) == (201, 200)
    assert first.json()["id"] == second.json()["id"]
    assert db_session.scalar(select(func.count()).select_from(Payment)) == 1


def test_same_key_with_different_data_returns_409(client):
    body = payment_body()
    pay(client, body, key="k-2")

    response = pay(client, {**body, "amount": "1.00"}, key="k-2")

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "IDEMPOTENCY_KEY_REUSED"


def test_second_payment_for_the_same_order_returns_409(client):
    body = payment_body()
    pay(client, body, key="k-3")

    response = pay(client, body, key="k-4")

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "ORDER_ALREADY_HAS_PAYMENT"


def test_rejected_payment_cannot_be_retried_for_the_same_order(client):
    body = payment_body(simulation="REJECT")
    pay(client, body, key="k-5")

    retry = pay(client, {**body, "simulation": "APPROVE"}, key="k-6")

    assert retry.status_code == 409


# --- disabled simulation ----------------------------------------------------

def test_disabled_simulation_returns_503_and_records_nothing(client, db_session, monkeypatch):
    monkeypatch.setattr(settings, "payment_simulation_enabled", False)

    response = pay(client)

    assert response.status_code == 503
    assert db_session.scalar(select(func.count()).select_from(Payment)) == 0


# --- lookup by order (reconciliation) ---------------------------------------

def test_read_payment_by_order(client):
    body = payment_body()
    created = pay(client, body).json()

    found = client.get("/internal/payments", params={"order_id": body["order_id"]}, headers=INTERNAL)

    assert found.json()["id"] == created["id"]
    assert client.get("/internal/payments", params={"order_id": str(uuid.uuid4())},
                      headers=INTERNAL).status_code == 404
