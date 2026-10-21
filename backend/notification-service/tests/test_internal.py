"""POST /internal/notifications: registration, idempotency, validation and internal key."""
import uuid

import pytest
from sqlalchemy import func, select

from app.models import Notification
from tests.conftest import bearer, event_body, send


def count(db_session, event_id):
    return db_session.scalar(select(func.count()).select_from(Notification)
                             .where(Notification.event_id == event_id))


def total(db_session):
    # The table may already hold real data: tests compare before and after
    return db_session.scalar(select(func.count()).select_from(Notification))


# --- Registration and idempotency ---------------------------------------------

def test_new_event_creates_an_unread_notification(client):
    body = event_body(user_id=7, amount="149.90")

    response = send(client, body)

    assert response.status_code == 201
    data = response.json()
    assert data["event_id"] == body["event_id"]
    assert (data["user_id"], data["type"], data["amount"], data["currency"]) == (7, "ORDER_PAID", "149.90", "USD")
    assert data["title"] == "Pago confirmado"
    assert "149.90 USD" in data["message"]
    assert (data["is_read"], data["read_at"]) == (False, None)


@pytest.mark.parametrize("kind, title", [("PAYMENT_REJECTED", "Pago rechazado"),
                                         ("ORDER_CANCELLED", "Pedido cancelado")])
def test_each_type_uses_its_own_template(client, kind, title):
    assert send(client, event_body(kind)).json()["title"] == title


def test_identical_repeat_returns_the_original(client, db_session):
    body = event_body()
    first = send(client, body).json()

    again = send(client, body)

    assert again.status_code == 200
    assert again.json() == first  # same id, texts, dates and read state
    assert count(db_session, body["event_id"]) == 1


def test_same_amount_written_differently_is_the_same_event(client):
    order_id = uuid.uuid4()
    send(client, event_body(order_id=order_id, amount="149.90"))

    # 149.9 and 149.90 are the same amount: a retry, not a conflict
    assert send(client, event_body(order_id=order_id, amount="149.9")).status_code == 200
    assert send(client, event_body(order_id=order_id, amount=149.9)).status_code == 200


def test_repeat_does_not_change_a_read_notification(client):
    body = event_body(user_id=7)
    created = send(client, body).json()
    read = client.patch(f"/notifications/{created['id']}/read", headers=bearer(7)).json()

    again = send(client, body).json()

    assert (again["is_read"], again["read_at"]) == (True, read["read_at"])
    assert again["created_at"] == created["created_at"]


@pytest.mark.parametrize("change", [{"user_id": 8}, {"amount": "150.00"}])
def test_same_event_id_with_different_data_is_a_conflict(client, change):
    body = event_body()
    original = send(client, body).json()

    response = send(client, {**body, **change})

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "EVENT_CONFLICT"
    # Nothing was modified
    stored = client.get("/notifications", headers=bearer(1, "admin")).json()["items"]
    assert [n for n in stored if n["id"] == original["id"]] == [original]


def test_cancelled_order_with_and_without_amount(client):
    assert send(client, event_body("ORDER_CANCELLED", amount=None, currency=None)).status_code == 201
    assert send(client, event_body("ORDER_CANCELLED")).status_code == 201


# --- Validation (422, before reaching the database) ----------------------------

@pytest.mark.parametrize("changes", [
    {"event_id": "not-the-format"},
    {"event_id": f"{uuid.uuid4()}:ORDER_PAID"},                  # another order
    {"event_id": "x" * 101},
    {"type": "ORDER_SHIPPED"},
    {"user_id": 0},
    {"user_id": 2_147_483_648},                                  # larger than INTEGER in user-service
    {"amount": "10.005"},                                        # 3 decimals: rejected, not rounded
    {"amount": 10.005},
    {"amount": "0"},
    {"amount": "-1.00"},
    {"amount": "NaN"},
    {"amount": "10000000000.00"},                                # does not fit NUMERIC(12,2)
    {"currency": "EUR"},
    {"currency": None},                                          # amount without currency
    {"amount": None, "currency": None},                          # ORDER_PAID needs an amount
    {"title": "Hola"},                                           # texts are not accepted from outside
    {"card_number": "4111111111111111"},
])
def test_invalid_events_are_rejected(client, db_session, changes):
    body = {**event_body(), **changes}
    before = total(db_session)

    assert send(client, body).status_code == 422
    assert total(db_session) == before


def test_event_id_must_match_the_type(client):
    order_id = uuid.uuid4()
    body = event_body("ORDER_PAID", order_id=order_id, event_id=f"{order_id}:ORDER_CANCELLED")

    assert send(client, body).status_code == 422


def test_payment_rejected_needs_an_amount(client):
    assert send(client, event_body("PAYMENT_REJECTED", amount=None, currency=None)).status_code == 422


def test_cancelled_order_cannot_have_only_one_of_amount_and_currency(client):
    assert send(client, event_body("ORDER_CANCELLED", currency=None)).status_code == 422
    assert send(client, event_body("ORDER_CANCELLED", amount=None)).status_code == 422


def test_json_number_amount_is_kept_exact(client):
    assert send(client, event_body(amount=0.1)).json()["amount"] == "0.10"


# --- Internal key ---------------------------------------------------------------

@pytest.mark.parametrize("headers", [
    {},
    {"X-Internal-Key": "wrong-key"},
    {"X-Internal-Key": ""},
])
def test_internal_key_is_required(client, db_session, headers):
    before = total(db_session)

    response = send(client, event_body(), headers=headers)

    assert response.status_code == 403
    assert total(db_session) == before


def test_a_user_token_is_not_an_internal_key(client):
    assert send(client, event_body(), headers=bearer(1, "admin")).status_code == 403
