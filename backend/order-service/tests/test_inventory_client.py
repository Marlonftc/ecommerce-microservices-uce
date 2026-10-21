import uuid

import pytest

from app.clients import inventory_client as ic


def reservation(order_id, status="RESERVED"):
    # Same shape as ReservationOut in inventory-service
    return {"order_id": str(order_id), "status": status, "items": [{"product_id": 1, "quantity": 2}]}


def conflict(code, **extra):
    return (409, {"detail": {"code": code, "message": "x", **extra}})


def test_reserve_sends_the_agreed_body(fake):
    order_id = uuid.uuid4()
    service = fake(ic, (201, reservation(order_id)))

    result = ic.reserve(order_id, [(3, 1), (1, 2)])

    assert service.last.method == "POST"
    assert service.last.url.path == "/internal/reservations"
    assert service.last_json() == {"order_id": str(order_id),
                                   "items": [{"product_id": 1, "quantity": 2},
                                             {"product_id": 3, "quantity": 1}]}
    assert (result.status, result.created) == ("RESERVED", True)


def test_repeated_reservation_is_not_created_again(fake):
    order_id = uuid.uuid4()
    fake(ic, (200, reservation(order_id)))

    assert ic.reserve(order_id, [(1, 2)]).created is False


@pytest.mark.parametrize("code, error", [
    ("RESERVATION_RELEASED", ic.ReservationReleased),
    ("RESERVATION_MISMATCH", ic.ReservationMismatch),
])
def test_reserve_business_conflicts(fake, code, error):
    fake(ic, conflict(code))

    with pytest.raises(error):
        ic.reserve(uuid.uuid4(), [(1, 2)])


def test_insufficient_stock_names_the_product(fake):
    fake(ic, conflict("INSUFFICIENT_STOCK", product_id=4))

    with pytest.raises(ic.InsufficientStock) as error:
        ic.reserve(uuid.uuid4(), [(4, 100)])

    assert error.value.product_id == 4


@pytest.mark.parametrize("count", [0, 51])
def test_reservation_size_limits(fake, count):
    service = fake(ic, (201, reservation(uuid.uuid4())))

    with pytest.raises(ValueError):
        ic.reserve(uuid.uuid4(), [(i, 1) for i in range(1, count + 1)])
    assert service.requests == []


def test_confirm(fake):
    order_id = uuid.uuid4()
    service = fake(ic, (200, reservation(order_id, "CONFIRMED")))

    assert ic.confirm(order_id).status == "CONFIRMED"
    assert service.last.url.path == f"/internal/reservations/{order_id}/confirm"


def test_confirm_unknown_reservation(fake):
    fake(ic, (404, {"detail": "Reservation not found"}))

    with pytest.raises(ic.ReservationNotFound):
        ic.confirm(uuid.uuid4())


def test_confirm_released_reservation(fake):
    fake(ic, conflict("INVALID_TRANSITION"))

    with pytest.raises(ic.InvalidTransition):
        ic.confirm(uuid.uuid4())


def test_release(fake):
    order_id = uuid.uuid4()
    service = fake(ic, (200, reservation(order_id, "RELEASED")))

    assert ic.release(order_id).status == "RELEASED"
    assert service.last.url.path == f"/internal/reservations/{order_id}/release"


def test_release_confirmed_reservation(fake):
    fake(ic, conflict("INVALID_TRANSITION"))

    with pytest.raises(ic.InvalidTransition):
        ic.release(uuid.uuid4())
