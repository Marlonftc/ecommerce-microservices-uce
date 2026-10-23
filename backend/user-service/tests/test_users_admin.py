"""Administration of accounts: PATCH /users/{id} and GET /users?ids=."""
import threading
import time
import uuid

import pytest
from sqlalchemy import delete, select, text, update

from app.models import User
from app.services import user_service
from app.tokens import create_access_token


def make_user(db, role="customer", is_active=True, name="Usuario"):
    # password_hash is never used here: tokens are signed directly
    user = User(email=f"{uuid.uuid4().hex[:12]}@correo.com", full_name=name,
                password_hash="not-used", role=role, is_active=is_active)
    db.add(user)
    db.commit()
    return user


def headers_for(user):
    return {"Authorization": f"Bearer {create_access_token(user.id, user.role)}"}


def reload(db, user_id):
    return db.scalar(select(User).where(User.id == user_id).execution_options(populate_existing=True))


@pytest.fixture
def admin(db_session):
    return make_user(db_session, role="admin", name="Admin A")


@pytest.fixture
def customer(db_session):
    return make_user(db_session, name="Cliente")


# --- PATCH: authentication and authorization ------------------------------------

def test_patch_requires_token(client, customer):
    response = client.patch(f"/users/{customer.id}", json={"is_active": False})

    assert response.status_code == 401


def test_patch_customer_gets_403(client, customer, db_session):
    other = make_user(db_session)

    response = client.patch(f"/users/{other.id}", json={"is_active": False}, headers=headers_for(customer))

    assert response.status_code == 403
    assert reload(db_session, other.id).is_active is True


def test_patch_with_token_of_deactivated_admin_gets_401(client, db_session, customer):
    old_admin = make_user(db_session, role="admin", is_active=False)

    response = client.patch(f"/users/{customer.id}", json={"is_active": False}, headers=headers_for(old_admin))

    assert response.status_code == 401


# --- PATCH: changes that work --------------------------------------------------

def test_admin_deactivates_customer_and_its_token_stops_working(client, admin, customer, db_session):
    customer_headers = headers_for(customer)
    assert client.get("/users/me", headers=customer_headers).status_code == 200

    response = client.patch(f"/users/{customer.id}", json={"is_active": False}, headers=headers_for(admin))

    assert response.status_code == 200
    body = response.json()
    assert body["is_active"] is False and body["role"] == "customer"
    assert "password_hash" not in body and "password" not in body
    assert client.get("/users/me", headers=customer_headers).status_code == 401


def test_admin_reactivates_customer(client, admin, db_session):
    inactive = make_user(db_session, is_active=False)

    response = client.patch(f"/users/{inactive.id}", json={"is_active": True}, headers=headers_for(admin))

    assert response.status_code == 200
    assert reload(db_session, inactive.id).is_active is True


def test_admin_promotes_customer_and_demotes_other_admin(client, admin, customer, db_session):
    other_admin = make_user(db_session, role="admin")

    promoted = client.patch(f"/users/{customer.id}", json={"role": "admin"}, headers=headers_for(admin))
    demoted = client.patch(f"/users/{other_admin.id}", json={"role": "customer"}, headers=headers_for(admin))

    assert promoted.status_code == demoted.status_code == 200
    assert reload(db_session, customer.id).role == "admin"
    assert reload(db_session, other_admin.id).role == "customer"


def test_demoted_admin_loses_admin_access_in_user_service_at_once(client, admin, db_session):
    other_admin = make_user(db_session, role="admin")
    old_headers = headers_for(other_admin)  # the token still says role=admin
    assert client.get("/users", headers=old_headers).status_code == 200

    client.patch(f"/users/{other_admin.id}", json={"role": "customer"}, headers=headers_for(admin))

    assert client.get("/users", headers=old_headers).status_code == 403


def test_both_fields_in_one_request(client, admin, customer, db_session):
    response = client.patch(f"/users/{customer.id}", json={"role": "admin", "is_active": False},
                            headers=headers_for(admin))

    assert response.status_code == 200
    assert (response.json()["role"], response.json()["is_active"]) == ("admin", False)


def test_repeating_current_values_returns_200_without_changes(client, admin, customer, db_session):
    response = client.patch(f"/users/{customer.id}", json={"role": "customer", "is_active": True},
                            headers=headers_for(admin))

    assert response.status_code == 200
    user = reload(db_session, customer.id)
    assert (user.role, user.is_active) == ("customer", True)


# --- PATCH: errors ------------------------------------------------------------

def test_unknown_user_gives_404(client, admin):
    response = client.patch("/users/2147483647", json={"is_active": False}, headers=headers_for(admin))

    assert response.status_code == 404


@pytest.mark.parametrize("path_id", ["0", "-1", "abc", "2147483648"])
def test_invalid_path_id_gives_422(client, admin, path_id):
    response = client.patch(f"/users/{path_id}", json={"is_active": False}, headers=headers_for(admin))

    assert response.status_code == 422


@pytest.mark.parametrize("body", [
    {},
    {"role": None},
    {"is_active": None},
    {"role": "superadmin"},
    {"is_active": "quizá"},
    {"is_active": "true"},
    {"is_active": 1},
    {"email": "otro@correo.com"},
    {"role": "admin", "password": "x"},
])
def test_invalid_body_gives_422_and_changes_nothing(client, admin, customer, db_session, body):
    response = client.patch(f"/users/{customer.id}", json=body, headers=headers_for(admin))

    assert response.status_code == 422
    user = reload(db_session, customer.id)
    assert (user.role, user.is_active) == ("customer", True)


def test_customer_with_invalid_body_still_gets_403(client, customer, db_session):
    other = make_user(db_session)

    response = client.patch(f"/users/{other.id}", json={}, headers=headers_for(customer))

    assert response.status_code == 403


@pytest.mark.parametrize("body", [
    {"is_active": False},
    {"role": "customer"},
    {"role": "admin"},                    # no effect, still refused
    {"role": "admin", "is_active": True},  # no effect, still refused
])
def test_admin_cannot_modify_own_account(client, admin, db_session, body):
    response = client.patch(f"/users/{admin.id}", json=body, headers=headers_for(admin))

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "CANNOT_MODIFY_SELF"
    user = reload(db_session, admin.id)
    assert (user.role, user.is_active) == ("admin", True)


# --- service: revalidation and invariant ----------------------------------------

def test_actor_deactivated_while_waiting_is_refused(db_session):
    actor = make_user(db_session, role="admin")
    target = make_user(db_session, role="admin")
    assert actor.is_active  # the object in the session says "active"
    # Another transaction deactivates the actor; the loaded object is not refreshed
    db_session.execute(update(User).where(User.id == actor.id).values(is_active=False)
                       .execution_options(synchronize_session=False))
    assert actor.is_active is True

    with pytest.raises(user_service.ActorNotAdmin):
        user_service.update_user(db_session, actor.id, target.id, {"role": "customer"})

    assert reload(db_session, target.id).role == "admin"


def test_actor_demoted_while_waiting_is_refused(db_session):
    actor = make_user(db_session, role="admin")
    target = make_user(db_session)
    db_session.execute(update(User).where(User.id == actor.id).values(role="customer")
                       .execution_options(synchronize_session=False))

    with pytest.raises(user_service.ActorNotAdmin):
        user_service.update_user(db_session, actor.id, target.id, {"role": "admin"})

    assert reload(db_session, target.id).role == "customer"


def test_last_active_admin_invariant(db_session):
    # Inside the rolled back transaction: no active administrators at all
    db_session.execute(update(User).where(User.role == "admin").values(is_active=False))
    only_admin = make_user(db_session, role="admin")

    with pytest.raises(user_service.LastActiveAdmin):
        user_service._ensure_admin_remains(db_session, only_admin.id)

    make_user(db_session, role="admin")
    user_service._ensure_admin_remains(db_session, only_admin.id)  # does not raise


# --- GET /users?ids= ----------------------------------------------------------

def test_ids_requires_token_and_admin(client, customer):
    assert client.get("/users", params={"ids": str(customer.id)}).status_code == 401
    response = client.get("/users", params={"ids": str(customer.id)}, headers=headers_for(customer))
    assert response.status_code == 403


def test_ids_returns_existing_users_ordered_without_duplicates(client, admin, db_session):
    first = make_user(db_session, name="Uno")
    second = make_user(db_session, name="Dos")
    # Updating the first row stores a new version after the second one in the table,
    # so without ORDER BY a sequential scan would return them in the wrong order
    db_session.execute(text("SET LOCAL enable_indexscan = off"))
    db_session.execute(text("SET LOCAL enable_bitmapscan = off"))
    db_session.execute(update(User).where(User.id == first.id).values(full_name="Uno"))
    db_session.commit()
    ids = f"{second.id}, {first.id},{second.id},2147483647"

    response = client.get("/users", params={"ids": ids}, headers=headers_for(admin))

    assert response.status_code == 200
    body = response.json()
    assert [u["id"] for u in body] == [first.id, second.id]
    assert body[0]["full_name"] == "Uno"
    assert set(body[0]) == {"id", "email", "full_name", "role", "is_active", "created_at"}


def test_ids_includes_inactive_users(client, admin, db_session):
    inactive = make_user(db_session, is_active=False)

    body = client.get("/users", params={"ids": str(inactive.id)}, headers=headers_for(admin)).json()

    assert [(u["id"], u["is_active"]) for u in body] == [(inactive.id, False)]


def test_ids_only_unknown_returns_empty_list(client, admin):
    response = client.get("/users", params={"ids": "2147483647"}, headers=headers_for(admin))

    assert response.status_code == 200 and response.json() == []


def test_ids_accepts_100_values_counting_duplicates(client, admin):
    response = client.get("/users", params={"ids": ",".join(["1"] * 100)}, headers=headers_for(admin))

    assert response.status_code == 200


def test_parse_ids_removes_duplicates_and_sorts():
    from app.routers.users import parse_ids

    assert parse_ids(" 30,2,30, 7") == [2, 7, 30]


@pytest.mark.parametrize("ids", [
    "", " ", "1,,2", "1,", "a", "1,b", "0", "-1", "+1", "1.5", "١",
    "2147483648", ",".join(["1"] * 101), ",".join(str(i) for i in range(1, 102)),
])
def test_invalid_ids_give_422(client, admin, ids):
    response = client.get("/users", params={"ids": ids}, headers=headers_for(admin))

    assert response.status_code == 422


@pytest.mark.parametrize("extra", [{"limit": 10}, {"offset": 0}, {"limit": 50, "offset": 5}])
def test_ids_cannot_be_combined_with_pagination(client, admin, extra):
    response = client.get("/users", params={"ids": "1", **extra}, headers=headers_for(admin))

    assert response.status_code == 422


# --- compatibility of the paginated list ----------------------------------------

def test_list_without_parameters_is_unchanged(client, admin, db_session):
    for _ in range(3):
        make_user(db_session)

    response = client.get("/users", headers=headers_for(admin))

    assert response.status_code == 200
    ids = [u["id"] for u in response.json()]
    assert ids == sorted(ids) and 0 < len(ids) <= 50


def test_list_limit_and_offset_still_work(client, admin, db_session):
    for _ in range(3):
        make_user(db_session)
    all_ids = [u["id"] for u in client.get("/users", params={"limit": 100}, headers=headers_for(admin)).json()]

    page = client.get("/users", params={"limit": 2, "offset": 1}, headers=headers_for(admin))

    assert page.status_code == 200
    assert [u["id"] for u in page.json()] == all_ids[1:3]


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 101}, {"offset": -1}, {"limit": "x"}])
def test_list_invalid_pagination_still_gives_422(client, admin, params):
    assert client.get("/users", params=params, headers=headers_for(admin)).status_code == 422


# --- real race: two administrators demote each other ---------------------------

def test_two_admins_demoting_each_other_at_the_same_time():
    """Real committed data, independent sessions and real HTTP requests.

    A third connection holds the advisory lock until both requests are waiting
    for it, so the two PATCH requests truly overlap. Only one can win; the other
    is refused with 403 when its author is read again under the lock.
    """
    from fastapi.testclient import TestClient

    from app.database import SessionLocal, engine
    from app.main import app

    try:
        engine.connect().close()
    except Exception:
        pytest.skip("database not available")

    tag = uuid.uuid4().hex[:8]
    with SessionLocal() as db:
        a = User(email=f"race-a-{tag}@correo.com", full_name="Admin A", password_hash="not-used", role="admin")
        b = User(email=f"race-b-{tag}@correo.com", full_name="Admin B", password_hash="not-used", role="admin")
        db.add_all([a, b])
        db.commit()
        a_id, b_id = a.id, b.id

    results: dict[str, int] = {}

    def patch(name: str, actor_id: int, target_id: int) -> None:
        with TestClient(app) as http:
            headers = {"Authorization": f"Bearer {create_access_token(actor_id, 'admin')}"}
            results[name] = http.patch(f"/users/{target_id}", json={"role": "customer"}, headers=headers).status_code

    holder = engine.connect()
    try:
        holder.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": user_service.ROLE_CHANGE_LOCK_KEY})
        threads = [threading.Thread(target=patch, args=("A", a_id, b_id)),
                   threading.Thread(target=patch, args=("B", b_id, a_id))]
        for t in threads:
            t.start()

        # Wait until both requests are blocked on the advisory lock
        waiting = 0
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            with engine.connect() as probe:
                waiting = probe.scalar(text(
                    "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND NOT granted AND objid = :k"),
                    {"k": user_service.ROLE_CHANGE_LOCK_KEY})
            if waiting == 2:
                break
            time.sleep(0.05)
        assert waiting == 2, "both requests should be waiting for the lock"

        holder.commit()  # releases the lock: the two requests now compete
        for t in threads:
            t.join(timeout=15)

        assert sorted(results.values()) == [200, 403]
        winner = "A" if results["A"] == 200 else "B"
        winner_id, loser_id = (a_id, b_id) if winner == "A" else (b_id, a_id)
        with SessionLocal() as db:
            w, loser = db.get(User, winner_id), db.get(User, loser_id)
            # Only the winner's change was applied, nothing partial
            assert (w.role, w.is_active) == ("admin", True)
            assert (loser.role, loser.is_active) == ("customer", True)
            active_admins = db.scalar(text("SELECT count(*) FROM users WHERE role = 'admin' AND is_active"))
            assert active_admins >= 1
    finally:
        holder.close()
        with SessionLocal() as db:
            db.execute(delete(User).where(User.id.in_([a_id, b_id])))
            db.commit()
