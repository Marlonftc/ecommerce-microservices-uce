"""Calls to payment-service.

The Idempotency-Key sent here is NOT the key of the customer: it is built
from the order id, so every attempt for the same order (the original
request, a retry, the worker) uses the same key and payment-service
returns the same payment.
"""
import uuid
from dataclasses import dataclass
from decimal import Decimal

from pydantic import BaseModel, ValidationError

from app.clients.base import ServiceClient, UnexpectedResponse, conflict_from, json_body
from app.config import settings

client = ServiceClient("payment-service", settings.payment_service_url)


def payment_key(order_id: uuid.UUID) -> str:
    """Deterministic internal key: one per order."""
    return f"order:{order_id}"


class IdempotencyKeyReused(Exception):
    """The key was used with different payment data."""


class OrderAlreadyHasPayment(Exception):
    """The order already has a payment created with another key."""


@dataclass(frozen=True)
class PaymentResult:
    id: uuid.UUID
    order_id: uuid.UUID
    status: str  # PENDING, APPROVED or REJECTED
    amount: Decimal
    currency: str
    simulation: str
    created: bool


class _Payment(BaseModel):
    id: uuid.UUID
    order_id: uuid.UUID
    status: str
    amount: Decimal
    currency: str
    simulation: str


def _result(response, created: bool = False) -> PaymentResult:
    try:
        p = _Payment.model_validate(json_body(client.name, response))
    except ValidationError:
        raise UnexpectedResponse(client.name, response.status_code, "unexpected body")
    return PaymentResult(p.id, p.order_id, p.status, p.amount, p.currency, p.simulation, created)


def create_payment(*, order_id: uuid.UUID, user_id: int, amount: Decimal, currency: str,
                   simulation: str) -> PaymentResult:
    """Repeating it with the same data returns the same payment (200)."""
    body = {
        "order_id": str(order_id),
        "user_id": user_id,
        "amount": str(amount),  # as text: exact, never a float
        "currency": currency,
        "simulation": simulation,
    }
    response = client.request("POST", "/internal/payments", json=body,
                              headers={"Idempotency-Key": payment_key(order_id)})
    if response.status_code in (200, 201):
        return _result(response, created=response.status_code == 201)
    if response.status_code == 409:
        conflict = conflict_from(client.name, response)
        if conflict.code == "IDEMPOTENCY_KEY_REUSED":
            raise IdempotencyKeyReused
        if conflict.code == "ORDER_ALREADY_HAS_PAYMENT":
            raise OrderAlreadyHasPayment
    # 503 (simulation disabled) is already a ServiceUnavailable: it may be enabled later
    raise UnexpectedResponse(client.name, response.status_code)


def get_payment_by_order(order_id: uuid.UUID) -> PaymentResult | None:
    """The real state of the payment of an order, or None if it was never created."""
    response = client.request("GET", "/internal/payments", params={"order_id": str(order_id)})
    if response.status_code == 200:
        return _result(response)
    if response.status_code == 404:
        return None
    raise UnexpectedResponse(client.name, response.status_code)
