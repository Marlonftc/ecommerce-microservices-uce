import uuid

from app.models import Stock
from tests.conftest import INTERNAL


def reserve(client, order_id=None, items=None, headers=INTERNAL):
    order_id = order_id or uuid.uuid4()
    items = items if items is not None else [{"product_id": 1, "quantity": 2}, {"product_id": 3, "quantity": 1}]
    return client.post("/internal/reservations", json={"order_id": str(order_id), "items": items}, headers=headers)


def action(client, order_id, name):
    return client.post(f"/internal/reservations/{order_id}/{name}", headers=INTERNAL)


def stock(db_session, product_id):
    db_session.expire_all()
    s = db_session.get(Stock, product_id)
    return (s.available, s.reserved)


# --- protection -------------------------------------------------------------

def test_internal_routes_require_the_internal_key(client, admin_headers):
    assert reserve(client, headers={}).status_code == 403
    assert reserve(client, headers={"X-Internal-Key": "wrong"}).status_code == 403
    assert reserve(client, headers=admin_headers).status_code == 403


# --- reserve ----------------------------------------------------------------

def test_reserve_moves_units_from_available_to_reserved(client, db_session):
    response = reserve(client)

    assert response.status_code == 201
    assert response.json()["status"] == "RESERVED"
    assert stock(db_session, 1) == (13, 2)
    assert stock(db_session, 3) == (19, 1)


def test_repeating_the_same_reservation_does_not_reserve_twice(client, db_session):
    order_id = uuid.uuid4()
    first = reserve(client, order_id)
    second = reserve(client, order_id)

    assert (first.status_code, second.status_code) == (201, 200)
    assert stock(db_session, 1) == (13, 2)


def test_same_order_with_different_content_returns_409(client, db_session):
    order_id = uuid.uuid4()
    reserve(client, order_id)

    response = reserve(client, order_id, items=[{"product_id": 1, "quantity": 5}])

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "RESERVATION_MISMATCH"
    assert stock(db_session, 1) == (13, 2)


def test_reservation_is_all_or_nothing(client, db_session):
    # Product 1 has enough units, product 2 does not (8 available)
    response = reserve(client, items=[{"product_id": 1, "quantity": 2}, {"product_id": 2, "quantity": 9}])

    assert response.status_code == 409
    assert response.json()["detail"] == {"code": "INSUFFICIENT_STOCK", "message": "Not enough units", "product_id": 2}
    assert stock(db_session, 1) == (15, 0)  # nothing was reserved
    assert stock(db_session, 2) == (8, 0)


def test_product_without_stock_cannot_be_reserved(client):
    response = reserve(client, items=[{"product_id": 999, "quantity": 1}])

    assert response.json()["detail"]["code"] == "INSUFFICIENT_STOCK"


def test_repeated_product_in_one_reservation_is_rejected(client):
    items = [{"product_id": 1, "quantity": 1}, {"product_id": 1, "quantity": 1}]

    assert reserve(client, items=items).status_code == 422


def test_reservations_work_while_product_service_is_down(client, db_session, product_service):
    product_service["down"] = True

    assert reserve(client).status_code == 201
    assert product_service["calls"] == 0


# --- confirm and release ----------------------------------------------------

def test_confirm_takes_the_units_out_of_the_warehouse(client, db_session):
    order_id = uuid.uuid4()
    reserve(client, order_id)

    response = action(client, order_id, "confirm")

    assert response.json()["status"] == "CONFIRMED"
    assert stock(db_session, 1) == (13, 0)
    assert action(client, order_id, "confirm").status_code == 200  # repeating is safe
    assert stock(db_session, 1) == (13, 0)


def test_release_gives_the_units_back(client, db_session):
    order_id = uuid.uuid4()
    reserve(client, order_id)

    response = action(client, order_id, "release")

    assert response.json()["status"] == "RELEASED"
    assert stock(db_session, 1) == (15, 0)
    assert action(client, order_id, "release").status_code == 200  # repeating is safe
    assert stock(db_session, 1) == (15, 0)


def test_released_reservation_cannot_be_confirmed(client):
    order_id = uuid.uuid4()
    reserve(client, order_id)
    action(client, order_id, "release")

    response = action(client, order_id, "confirm")

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "INVALID_TRANSITION"


def test_confirmed_reservation_cannot_be_released(client, db_session):
    order_id = uuid.uuid4()
    reserve(client, order_id)
    action(client, order_id, "confirm")

    assert action(client, order_id, "release").status_code == 409
    assert stock(db_session, 1) == (13, 0)


def test_confirm_unknown_order_returns_404(client):
    assert action(client, uuid.uuid4(), "confirm").status_code == 404


# --- tombstone --------------------------------------------------------------

def test_release_before_reserve_blocks_a_late_reservation(client, db_session):
    order_id = uuid.uuid4()

    tombstone = action(client, order_id, "release")
    late = reserve(client, order_id)

    assert tombstone.json() == {"order_id": str(order_id), "status": "RELEASED", "items": []}
    assert late.status_code == 409
    assert late.json()["detail"]["code"] == "RESERVATION_RELEASED"
    assert stock(db_session, 1) == (15, 0)


def test_read_reservation(client):
    order_id = uuid.uuid4()
    reserve(client, order_id)

    assert client.get(f"/internal/reservations/{order_id}", headers=INTERNAL).json()["status"] == "RESERVED"
    assert client.get(f"/internal/reservations/{uuid.uuid4()}", headers=INTERNAL).status_code == 404
