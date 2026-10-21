"""GET /notifications and PATCH /notifications/{id}/read."""
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.models import Notification
from tests.conftest import bearer, event_body, make_token, send

# Test users far from real ids: the database may already hold real notifications
OWNER, OTHER = 900001, 900002


def add(db_session, user_id=OWNER, created_at=None, is_read=False):
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
    return make_token(OWNER, key=pem)


@pytest.mark.parametrize("headers", [
    {},
    {"Authorization": "Bearer abc"},
    {"Authorization": f"Bearer {make_token(OWNER, minutes=-1)}"},   # expired
    {"Authorization": f"Bearer {other_key_token()}"},            # not signed by user-service
])
def test_token_is_required(client, headers):
    assert client.get("/notifications", headers=headers).status_code == 401
    assert client.patch(f"/notifications/{uuid.uuid4()}/read", headers=headers).status_code == 401


# --- Listing ------------------------------------------------------------------------

def test_customer_sees_only_their_own(client, db_session):
    mine = add(db_session, user_id=OWNER)
    add(db_session, user_id=OTHER)

    data = client.get("/notifications", headers=bearer(OWNER)).json()

    assert [n["id"] for n in data["items"]] == [mine]
    assert data["total"] == 1


def test_admin_sees_all(client, db_session):
    a = add(db_session, user_id=OWNER)
    b = add(db_session, user_id=OTHER)

    data = client.get("/notifications", params={"size": 100}, headers=bearer(1, "admin")).json()

    # Other users' notifications included (real data may exist besides these two)
    assert {a, b} <= {n["id"] for n in data["items"]}
    assert data["total"] >= 2


def test_newest_first(client, db_session):
    now = datetime.now(UTC)
    old = add(db_session, created_at=now - timedelta(days=2))
    new = add(db_session, created_at=now)
    middle = add(db_session, created_at=now - timedelta(days=1))

    items = client.get("/notifications", headers=bearer(OWNER)).json()["items"]

    assert [n["id"] for n in items] == [new, middle, old]


def test_pages_do_not_repeat_or_skip_rows_with_equal_dates(client, db_session):
    # All rows with the SAME created_at: only the id tie-breaker keeps the order stable
    same_time = datetime.now(UTC)
    ids = {add(db_session, created_at=same_time) for _ in range(5)}

    pages = [client.get("/notifications", params={"page": p, "size": 2}, headers=bearer(OWNER)).json()
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

    data = client.get("/notifications", params={"unread_only": True}, headers=bearer(OWNER)).json()

    assert [n["id"] for n in data["items"]] == [unread]
    assert data["total"] == 1


@pytest.mark.parametrize("params", [{"page": 0}, {"size": 0}, {"size": 101}])
def test_invalid_pagination_is_rejected(client, params):
    assert client.get("/notifications", params=params, headers=bearer(OWNER)).status_code == 422


# --- Mark as read --------------------------------------------------------------------

def test_owner_marks_as_read(client, db_session):
    nid = add(db_session, user_id=OWNER)

    response = client.patch(f"/notifications/{nid}/read", headers=bearer(OWNER))

    assert response.status_code == 200
    assert response.json()["is_read"] is True
    assert response.json()["read_at"] is not None


def test_marking_again_keeps_the_first_read_date(client):
    nid = send(client, event_body(user_id=OWNER)).json()["id"]
    first = client.patch(f"/notifications/{nid}/read", headers=bearer(OWNER)).json()

    second = client.patch(f"/notifications/{nid}/read", headers=bearer(OWNER))

    assert second.status_code == 200
    assert second.json()["read_at"] == first["read_at"]


def test_another_users_notification_is_not_found(client, db_session):
    nid = add(db_session, user_id=OTHER)

    response = client.patch(f"/notifications/{nid}/read", headers=bearer(OWNER))

    # Same answer as a notification that does not exist: nothing is revealed
    assert response.status_code == 404
    admin_view = client.get("/notifications", params={"size": 100}, headers=bearer(1, "admin")).json()["items"]
    assert next(n for n in admin_view if n["id"] == nid)["is_read"] is False  # it was not modified


def test_admin_cannot_mark_another_users_notification(client, db_session):
    nid = add(db_session, user_id=OTHER)

    assert client.patch(f"/notifications/{nid}/read", headers=bearer(1, "admin")).status_code == 404


def test_unknown_notification_is_not_found(client):
    assert client.patch(f"/notifications/{uuid.uuid4()}/read", headers=bearer(OWNER)).status_code == 404
    assert client.patch("/notifications/abc/read", headers=bearer(OWNER)).status_code == 422
