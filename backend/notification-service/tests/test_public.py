"""GET /notifications and PATCH /notifications/{id}/read."""
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.models import Notification
from tests.conftest import bearer, event_body, make_token, send


def add(db_session, user_id=7, created_at=None, is_read=False):
    order_id = uuid.uuid4()
    n = Notification(event_id=f"{order_id}:ORDER_CANCELLED", user_id=user_id, order_id=order_id,
                     type="ORDER_CANCELLED", title="t", message="m", is_read=is_read,
                     read_at=datetime.now(UTC) if is_read else None)
    if created_at is not None:
        n.created_at = created_at
    db_session.add(n)
    db_session.flush()
    return str(n.id)


# --- Authentication -------------------------------------------------------------

def other_key_token():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption())
    return make_token(7, key=pem)


@pytest.mark.parametrize("headers", [
    {},
    {"Authorization": "Bearer abc"},
    {"Authorization": f"Bearer {make_token(7, minutes=-1)}"},   # expired
    {"Authorization": f"Bearer {other_key_token()}"},            # not signed by user-service
])
def test_token_is_required(client, headers):
    assert client.get("/notifications", headers=headers).status_code == 401
    assert client.patch(f"/notifications/{uuid.uuid4()}/read", headers=headers).status_code == 401


# --- Listing ------------------------------------------------------------------------

def test_customer_sees_only_their_own(client, db_session):
    mine = add(db_session, user_id=7)
    add(db_session, user_id=8)

    data = client.get("/notifications", headers=bearer(7)).json()

    assert [n["id"] for n in data["items"]] == [mine]
    assert data["total"] == 1


def test_admin_sees_all(client, db_session):
    a = add(db_session, user_id=7)
    b = add(db_session, user_id=8)

    data = client.get("/notifications", headers=bearer(1, "admin")).json()

    assert {n["id"] for n in data["items"]} == {a, b}
    assert data["total"] == 2


def test_newest_first(client, db_session):
    now = datetime.now(UTC)
    old = add(db_session, created_at=now - timedelta(days=2))
    new = add(db_session, created_at=now)
    middle = add(db_session, created_at=now - timedelta(days=1))

    items = client.get("/notifications", headers=bearer(7)).json()["items"]

    assert [n["id"] for n in items] == [new, middle, old]


def test_pages_do_not_repeat_or_skip_rows_with_equal_dates(client, db_session):
    # All rows with the SAME created_at: only the id tie-breaker keeps the order stable
    same_time = datetime.now(UTC)
    ids = {add(db_session, created_at=same_time) for _ in range(5)}

    pages = [client.get("/notifications", params={"page": p, "size": 2}, headers=bearer(7)).json()
             for p in (1, 2, 3, 4)]
    seen = [n["id"] for page in pages for n in page["items"]]

    assert [len(page["items"]) for page in pages] == [2, 2, 1, 0]
    assert all(page["total"] == 5 for page in pages)
    assert sorted(seen) == sorted(ids)               # every row exactly once
    assert seen == sorted(ids, reverse=True)         # created_at DESC, then id DESC
    assert (pages[1]["page"], pages[1]["size"]) == (2, 2)


def test_unread_only_filter(client, db_session):
    unread = add(db_session, is_read=False)
    add(db_session, is_read=True)

    data = client.get("/notifications", params={"unread_only": True}, headers=bearer(7)).json()

    assert [n["id"] for n in data["items"]] == [unread]
    assert data["total"] == 1


@pytest.mark.parametrize("params", [{"page": 0}, {"size": 0}, {"size": 101}])
def test_invalid_pagination_is_rejected(client, params):
    assert client.get("/notifications", params=params, headers=bearer(7)).status_code == 422


# --- Mark as read --------------------------------------------------------------------

def test_owner_marks_as_read(client, db_session):
    nid = add(db_session, user_id=7)

    response = client.patch(f"/notifications/{nid}/read", headers=bearer(7))

    assert response.status_code == 200
    assert response.json()["is_read"] is True
    assert response.json()["read_at"] is not None


def test_marking_again_keeps_the_first_read_date(client):
    nid = send(client, event_body(user_id=7)).json()["id"]
    first = client.patch(f"/notifications/{nid}/read", headers=bearer(7)).json()

    second = client.patch(f"/notifications/{nid}/read", headers=bearer(7))

    assert second.status_code == 200
    assert second.json()["read_at"] == first["read_at"]


def test_another_users_notification_is_not_found(client, db_session):
    nid = add(db_session, user_id=8)

    response = client.patch(f"/notifications/{nid}/read", headers=bearer(7))

    # Same answer as a notification that does not exist: nothing is revealed
    assert response.status_code == 404
    admin_view = client.get("/notifications", headers=bearer(1, "admin")).json()["items"]
    assert admin_view[0]["is_read"] is False  # it was not modified


def test_admin_cannot_mark_another_users_notification(client, db_session):
    nid = add(db_session, user_id=8)

    assert client.patch(f"/notifications/{nid}/read", headers=bearer(1, "admin")).status_code == 404


def test_unknown_notification_is_not_found(client):
    assert client.patch(f"/notifications/{uuid.uuid4()}/read", headers=bearer(7)).status_code == 404
    assert client.patch("/notifications/abc/read", headers=bearer(7)).status_code == 422
