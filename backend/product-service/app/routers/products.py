from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Response, status

from app.dependencies import AdminUser, DbSession, OptionalUser
from app.schemas import ProductIn, ProductList, ProductOut, ProductUpdate, normalize_category
from app.services import product_service

router = APIRouter(prefix="/products", tags=["products"])

NOT_FOUND = {404: {"description": "Product not found"}}
ADMIN_ONLY = {401: {"description": "Missing, invalid or expired token"},
              403: {"description": "Admin role required"}}
SKU_CONFLICT = {409: {"description": "SKU already exists"}}


def _parse_ids(raw: str | None) -> list[int] | None:
    if raw is None:
        return None
    try:
        ids = [int(part) for part in raw.split(",") if part.strip()]
    except ValueError:
        raise HTTPException(status_code=422, detail="ids must be comma separated integers")
    if not ids or len(ids) > 100:
        raise HTTPException(status_code=422, detail="ids must contain between 1 and 100 values")
    return ids


@router.get("", response_model=ProductList, responses=ADMIN_ONLY)
def read_products(
    db: DbSession,
    user: OptionalUser,
    category: str | None = None,
    q: Annotated[str | None, Query(max_length=100, description="Search by name")] = None,
    ids: Annotated[str | None, Query(description="Comma separated ids, for example 1,2,3")] = None,
    include_inactive: Annotated[bool, Query(description="Admin only")] = False,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    if include_inactive and (user is None or not user.is_admin):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin role required")

    items, total = product_service.list_products(
        db,
        category=normalize_category(category) if category else None,
        search=q.strip() if q else None,
        ids=_parse_ids(ids),
        include_inactive=include_inactive,
        limit=limit,
        offset=offset,
    )
    return ProductList(items=items, total=total, limit=limit, offset=offset)


@router.get("/{product_id}", response_model=ProductOut, responses=NOT_FOUND)
def read_product(product_id: int, db: DbSession, user: OptionalUser):
    is_admin = user is not None and user.is_admin
    try:
        return product_service.get_product(db, product_id, include_inactive=is_admin)
    except product_service.ProductNotFound:
        raise HTTPException(status_code=404, detail="Product not found")


@router.post("", response_model=ProductOut, status_code=status.HTTP_201_CREATED,
             responses={**ADMIN_ONLY, **SKU_CONFLICT})
def create_product(data: ProductIn, db: DbSession, _: AdminUser):
    try:
        return product_service.create_product(db, data)
    except product_service.SkuAlreadyExists:
        raise HTTPException(status_code=409, detail="SKU already exists")


@router.put("/{product_id}", response_model=ProductOut,
            responses={**ADMIN_ONLY, **NOT_FOUND, **SKU_CONFLICT})
def replace_product(product_id: int, data: ProductUpdate, db: DbSession, _: AdminUser):
    try:
        return product_service.update_product(db, product_id, data)
    except product_service.ProductNotFound:
        raise HTTPException(status_code=404, detail="Product not found")
    except product_service.SkuAlreadyExists:
        raise HTTPException(status_code=409, detail="SKU already exists")


@router.delete("/{product_id}", status_code=status.HTTP_204_NO_CONTENT,
               responses={**ADMIN_ONLY, **NOT_FOUND})
def deactivate_product(product_id: int, db: DbSession, _: AdminUser):
    try:
        product_service.deactivate_product(db, product_id)
    except product_service.ProductNotFound:
        raise HTTPException(status_code=404, detail="Product not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
