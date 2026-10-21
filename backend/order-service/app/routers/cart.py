import logging
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query
from sqlalchemy.orm import Session

from app.clients import product_client
from app.clients.base import ServiceUnavailable, UnexpectedResponse
from app.dependencies import CurrentUser, DbSession
from app.schemas import CartItemIn, CartItemOut, CartOut
from app.services import cart_service as cs

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/cart", tags=["cart"])

ProductId = Annotated[int, Path(gt=0, le=2_147_483_647)]
ExpectedVersion = Annotated[int | None, Query(ge=0, description="Version the client last saw")]

ERRORS = {
    401: {"description": "Missing, invalid or expired token"},
    409: {"description": "CART_LOCKED, CART_CHANGED or CART_FULL"},
}


def _conflict(code: str, message: str) -> HTTPException:
    return HTTPException(status_code=409, detail={"code": code, "message": message})


def _priced(view: cs.CartView) -> CartOut:
    """Adds current prices from product-service. If the catalogue does not
    answer, the cart is still returned, without prices."""
    try:
        products = product_client.get_active_products(view.items)
    except (ServiceUnavailable, UnexpectedResponse) as exc:
        logger.warning("cart shown without prices: %s", exc)
        items = [CartItemOut(product_id=p, quantity=q) for p, q in sorted(view.items.items())]
        return CartOut(version=view.version, locked=view.locked, items=items,
                       prices_available=False, estimated_total=None)

    items, total = [], Decimal("0.00")
    for product_id, quantity in sorted(view.items.items()):
        product = products.get(product_id)
        if product is None:
            # Deactivated after it was added: shown, but not counted
            items.append(CartItemOut(product_id=product_id, quantity=quantity, available=False))
            continue
        subtotal = product.price * quantity
        total += subtotal
        items.append(CartItemOut(product_id=product_id, quantity=quantity, sku=product.sku,
                                 name=product.name, unit_price=product.price, subtotal=subtotal,
                                 available=True))
    return CartOut(version=view.version, locked=view.locked, items=items,
                   prices_available=True, estimated_total=total)


def _run(db: Session, action, *args) -> cs.CartView:
    try:
        return action(db, *args)
    except cs.CartLocked:
        raise _conflict("CART_LOCKED", "A checkout is in progress; the cart cannot be modified")
    except cs.CartChanged:
        raise _conflict("CART_CHANGED", "The cart changed since that version; read it again")
    except cs.CartFull:
        raise _conflict("CART_FULL", f"A cart can hold at most {cs.MAX_PRODUCTS} different products")


@router.get("", response_model=CartOut, responses=ERRORS)
def read_cart(db: DbSession, user: CurrentUser):
    view = cs.read_cart(db, user.id)
    db.commit()  # end the read transaction before calling product-service
    return _priced(view)


@router.put("/items/{product_id}", response_model=CartOut,
            responses={**ERRORS, 404: {"description": "PRODUCT_NOT_AVAILABLE"},
                       503: {"description": "product-service unavailable; the cart did not change"}})
def set_item(product_id: ProductId, data: CartItemIn, db: DbSession, user: CurrentUser):
    # Checked before touching the cart: no row is locked during the HTTP call
    try:
        available = product_client.get_active_products([product_id])
    except ServiceUnavailable:
        raise HTTPException(status_code=503, detail="product-service is not available, try again later")
    except UnexpectedResponse:
        raise HTTPException(status_code=502, detail="product-service gave an unexpected answer")
    if product_id not in available:
        raise HTTPException(status_code=404, detail={
            "code": "PRODUCT_NOT_AVAILABLE", "message": "The product does not exist or is not active"})

    view = _run(db, cs.set_quantity, user.id, product_id, data.quantity, data.expected_version)
    return _priced(view)


@router.delete("/items/{product_id}", response_model=CartOut, responses=ERRORS)
def remove_item(product_id: ProductId, db: DbSession, user: CurrentUser,
                expected_version: ExpectedVersion = None):
    """Idempotent: removing a product that is not in the cart changes nothing."""
    return _priced(_run(db, cs.remove_item, user.id, product_id, expected_version))


@router.delete("", response_model=CartOut, responses=ERRORS)
def clear_cart(db: DbSession, user: CurrentUser, expected_version: ExpectedVersion = None):
    """Empties the cart. The version keeps growing; it is never reset."""
    return _priced(_run(db, cs.clear, user.id, expected_version))
