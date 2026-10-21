"""Calls to inventory-service: reserve, confirm and release the units of an order.

All three operations are idempotent in inventory-service, so they can be
repeated safely after a timeout.
"""
import uuid
from collections.abc import Iterable
from dataclasses import dataclass

from pydantic import BaseModel, ValidationError

from app.clients.base import Conflict, ServiceClient, UnexpectedResponse, conflict_from, json_body
from app.config import settings

MAX_ITEMS = 50  # limit of a reservation in inventory-service

client = ServiceClient("inventory-service", settings.inventory_service_url)


class InsufficientStock(Exception):
    def __init__(self, product_id: int | None):
        super().__init__(f"not enough units of product {product_id}")
        self.product_id = product_id


class ReservationReleased(Exception):
    """The order was released (tombstone): it can never be reserved again."""


class ReservationMismatch(Exception):
    """A reservation for this order exists with other products or quantities."""


class InvalidTransition(Exception):
    """Confirm a released reservation, or release a confirmed one."""


class ReservationNotFound(Exception):
    pass


@dataclass(frozen=True)
class ReservationResult:
    order_id: uuid.UUID
    status: str  # RESERVED, CONFIRMED or RELEASED
    created: bool  # True only when this call created it (201)


class _Reservation(BaseModel):
    order_id: uuid.UUID
    status: str


def _result(response, created: bool = False) -> ReservationResult:
    try:
        data = _Reservation.model_validate(json_body(client.name, response))
    except ValidationError:
        raise UnexpectedResponse(client.name, response.status_code, "unexpected body")
    return ReservationResult(data.order_id, data.status, created)


def _raise_business_error(conflict: Conflict):
    if conflict.code == "INSUFFICIENT_STOCK":
        raise InsufficientStock(conflict.detail.get("product_id"))
    if conflict.code == "RESERVATION_RELEASED":
        raise ReservationReleased
    if conflict.code == "RESERVATION_MISMATCH":
        raise ReservationMismatch
    if conflict.code == "INVALID_TRANSITION":
        raise InvalidTransition(conflict.detail.get("message", ""))
    raise UnexpectedResponse(client.name, 409, f"unknown code {conflict.code}")


def reserve(order_id: uuid.UUID, items: Iterable[tuple[int, int]]) -> ReservationResult:
    """items: (product_id, quantity) pairs. All or nothing."""
    body_items = [{"product_id": p, "quantity": q} for p, q in sorted(items)]
    if not 1 <= len(body_items) <= MAX_ITEMS:
        raise ValueError(f"a reservation needs between 1 and {MAX_ITEMS} products")

    response = client.request("POST", "/internal/reservations",
                              json={"order_id": str(order_id), "items": body_items})
    if response.status_code in (200, 201):
        return _result(response, created=response.status_code == 201)
    if response.status_code == 409:
        _raise_business_error(conflict_from(client.name, response))
    raise UnexpectedResponse(client.name, response.status_code)


def confirm(order_id: uuid.UUID) -> ReservationResult:
    """The sale happened. Repeating it on a confirmed reservation is fine."""
    response = client.request("POST", f"/internal/reservations/{order_id}/confirm")
    if response.status_code == 200:
        return _result(response)
    if response.status_code == 404:
        raise ReservationNotFound
    if response.status_code == 409:
        _raise_business_error(conflict_from(client.name, response))
    raise UnexpectedResponse(client.name, response.status_code)


def release(order_id: uuid.UUID) -> ReservationResult:
    """Units go back to available. An unknown order leaves a tombstone."""
    response = client.request("POST", f"/internal/reservations/{order_id}/release")
    if response.status_code == 200:
        return _result(response)
    if response.status_code == 409:
        _raise_business_error(conflict_from(client.name, response))
    raise UnexpectedResponse(client.name, response.status_code)
