import uuid
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from app.clients import inventory_client
from app.clients.base import ServiceUnavailable, UnexpectedResponse
from app.dependencies import CurrentUser, DbSession
from app.models import Order
from app.schemas import CheckoutIn, OrderOut, OrderPage
from app.services import order_service as os_

router = APIRouter(prefix="/orders", tags=["orders"])

AUTH = {401: {"description": "Missing, invalid or expired token"}}


def _conflict(code: str, message: str, **extra) -> HTTPException:
    return HTTPException(status_code=409, detail={"code": code, "message": message, **extra})


def _out(db: Session, order: Order) -> OrderOut:
    out = OrderOut.model_validate({**{c: getattr(order, c) for c in OrderOut.model_fields if c != "items"},
                                   "items": os_.items_of(db, order.id)})
    db.commit()
    return out


@router.post(
    "",
    response_model=OrderOut,
    status_code=status.HTTP_201_CREATED,
    responses={
        **AUTH,
        200: {"description": "Same Idempotency-Key: the original order"},
        202: {"description": "Saved as RESERVING: inventory did not answer. Repeat the request "
                             "with the same Idempotency-Key, or read GET /orders/{id}"},
        409: {"description": "IDEMPOTENCY_KEY_REUSED, CART_CHANGED, CART_EMPTY, CART_LOCKED, "
                             "PRODUCT_NOT_AVAILABLE or INSUFFICIENT_STOCK"},
        503: {"description": "product-service unavailable; no order was created"},
    },
)
def create_order(data: CheckoutIn, db: DbSession, user: CurrentUser, response: Response,
                 idempotency_key: Annotated[str, Header(min_length=1, max_length=100)]):
    try:
        result = os_.checkout(db, user.id, idempotency_key, data.cart_version)
    except os_.IdempotencyKeyReused:
        raise _conflict("IDEMPOTENCY_KEY_REUSED", "This Idempotency-Key was used for another cart version")
    except os_.CartChanged:
        raise _conflict("CART_CHANGED", "The cart changed; read it again and confirm")
    except os_.CartEmpty:
        raise _conflict("CART_EMPTY", "The cart is empty")
    except os_.CheckoutInProgress:
        raise _conflict("CART_LOCKED", "Another order is in progress")
    except os_.ProductsNotAvailable as exc:
        raise _conflict("PRODUCT_NOT_AVAILABLE", "Some products are no longer available",
                        product_ids=exc.product_ids)
    except ServiceUnavailable as exc:
        raise HTTPException(status_code=503, detail=f"{exc.service} is not available, try again later")
    except UnexpectedResponse as exc:
        raise HTTPException(status_code=502, detail=f"{exc.service} gave an unexpected answer")
    except inventory_client.ReservationMismatch:
        # Should never happen: the order stays RESERVING and the error is logged
        raise HTTPException(status_code=502, detail="inventory-service has a different reservation for this order")

    order = result.order
    out = _out(db, order)
    if result.insufficient_product is not None:
        raise _conflict("INSUFFICIENT_STOCK", "Not enough units", product_id=result.insufficient_product,
                        order_id=str(order.id))
    response.headers["Location"] = f"/orders/{order.id}"
    if order.status == "RESERVING":
        response.status_code = status.HTTP_202_ACCEPTED
    elif not result.created:
        response.status_code = status.HTTP_200_OK
    return out


@router.get("", response_model=OrderPage, responses=AUTH)
def read_orders(db: DbSession, user: CurrentUser,
                page: Annotated[int, Query(ge=1)] = 1,
                size: Annotated[int, Query(ge=1, le=100)] = 20):
    """A customer sees only their orders; an admin sees all. Newest first."""
    orders, total = os_.list_for_user(db, user.id, user.is_admin, page, size)
    return OrderPage(items=[_out(db, o) for o in orders], total=total, page=page, size=size)


@router.get("/{order_id}", response_model=OrderOut,
            responses={**AUTH, 404: {"description": "Order not found"}})
def read_order(order_id: uuid.UUID, db: DbSession, user: CurrentUser):
    try:
        order = os_.get_for_user(db, order_id, user.id, user.is_admin)
    except os_.OrderNotFound:
        # Same answer when it does not exist and when it belongs to someone else
        raise HTTPException(status_code=404, detail="Order not found")
    return _out(db, order)
