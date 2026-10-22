"""Calls to notification-service. Only business data is sent: the texts
are built by notification-service with its own templates."""
import uuid
from decimal import Decimal

from app.clients.base import ServiceClient, UnexpectedResponse, conflict_from
from app.config import settings

client = ServiceClient("notification-service", settings.notification_service_url)

EVENT_TYPES = ("ORDER_PAID", "PAYMENT_REJECTED", "ORDER_CANCELLED")


def event_id(order_id: uuid.UUID, event_type: str) -> str:
    """Agreed format "<order_id>:<type>": the same event always has the same id."""
    return f"{order_id}:{event_type}"


class EventConflict(Exception):
    """The event_id exists with different data. Retrying will not change it."""


def send_event(*, order_id: uuid.UUID, user_id: int, event_type: str,
               amount: Decimal | None = None, currency: str | None = None) -> bool:
    """Returns True if the notification was created now, False if it existed."""
    if event_type not in EVENT_TYPES:
        raise ValueError(f"unknown event type {event_type}")
    body = {
        "event_id": event_id(order_id, event_type),
        "user_id": user_id,
        "order_id": str(order_id),
        "type": event_type,
        "amount": str(amount) if amount is not None else None,
        "currency": currency,
    }
    response = client.request("POST", "/internal/notifications", json=body)
    if response.status_code in (200, 201):
        return response.status_code == 201
    if response.status_code == 409:
        conflict = conflict_from(client.name, response)
        if conflict.code == "EVENT_CONFLICT":
            raise EventConflict
    raise UnexpectedResponse(client.name, response.status_code)
