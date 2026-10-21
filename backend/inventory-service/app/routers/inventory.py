from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Query, Response, status

from app.clients import product_client
from app.dependencies import AdminUser, DbSession, OptionalUser
from app.schemas import AdjustmentIn, AdjustmentOut, StockAdmin, StockPublic
from app.services import stock_service

router = APIRouter(prefix="/inventory", tags=["inventory"])

ADMIN_ONLY = {401: {"description": "Missing, invalid or expired token"},
              403: {"description": "Admin role required"}}


def _view(stock, is_admin: bool):
    return StockAdmin.model_validate(stock) if is_admin else StockPublic.model_validate(stock)


@router.get("", response_model=list[StockAdmin | StockPublic])
def read_many(
    db: DbSession,
    user: OptionalUser,
    ids: Annotated[str, Query(description="Comma separated product ids, for example 1,2,3")],
):
    try:
        product_ids = [int(part) for part in ids.split(",") if part.strip()]
    except ValueError:
        raise HTTPException(status_code=422, detail="ids must be comma separated integers")
    if not product_ids or len(product_ids) > 100:
        raise HTTPException(status_code=422, detail="ids must contain between 1 and 100 values")
    is_admin = user is not None and user.is_admin
    return [_view(s, is_admin) for s in stock_service.get_many(db, product_ids)]


@router.get("/{product_id}", response_model=StockAdmin | StockPublic,
            responses={404: {"description": "No stock registered for this product"}})
def read_one(product_id: int, db: DbSession, user: OptionalUser):
    stock = stock_service.get_stock(db, product_id)
    if stock is None:
        raise HTTPException(status_code=404, detail="No stock registered for this product")
    return _view(stock, user is not None and user.is_admin)


@router.post(
    "/{product_id}/adjustments",
    response_model=AdjustmentOut,
    status_code=status.HTTP_201_CREATED,
    responses={
        **ADMIN_ONLY,
        200: {"description": "Repeated request: the first result is returned, nothing changes"},
        404: {"description": "Product does not exist in product-service"},
        409: {"description": "Not enough units, or key reused with different data"},
        503: {"description": "product-service is not available"},
    },
)
def adjust(
    product_id: int,
    data: AdjustmentIn,
    db: DbSession,
    admin: AdminUser,
    response: Response,
    idempotency_key: Annotated[str, Header(min_length=1, max_length=100)],
):
    try:
        adjustment, applied = stock_service.adjust_stock(
            db,
            product_id=product_id,
            delta=data.delta,
            reason=data.reason,
            admin_user_id=admin.id,
            idempotency_key=idempotency_key,
            product_exists=product_client.product_exists,
        )
    except stock_service.ProductNotFound:
        raise HTTPException(status_code=404, detail="Product does not exist")
    except stock_service.NotEnoughStock:
        raise HTTPException(status_code=409, detail="Not enough available units for this adjustment")
    except stock_service.IdempotencyKeyReused:
        raise HTTPException(status_code=409, detail="Idempotency-Key already used with different data")
    except product_client.ProductServiceUnavailable:
        raise HTTPException(status_code=503, detail="product-service is not available, try again later")

    if not applied:
        response.status_code = status.HTTP_200_OK
    return adjustment


@router.get("/{product_id}/adjustments", response_model=list[AdjustmentOut], responses=ADMIN_ONLY)
def read_adjustments(
    product_id: int,
    db: DbSession,
    _: AdminUser,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    return stock_service.list_adjustments(db, product_id, limit, offset)
