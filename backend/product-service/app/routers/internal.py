from fastapi import APIRouter, HTTPException

from app.dependencies import DbSession
from app.internal_auth import InternalOnly
from app.schemas import ProductOut
from app.services import product_service

# Called only by other microservices. Every route requires X-Internal-Key.
router = APIRouter(prefix="/internal/products", tags=["internal"], dependencies=[InternalOnly])


@router.get("/{product_id}", response_model=ProductOut, responses={404: {"description": "Product not found"}})
def read_product_internal(product_id: int, db: DbSession):
    """Returns the product even if it is inactive (inventory can still adjust its stock)."""
    try:
        return product_service.get_product(db, product_id, include_inactive=True)
    except product_service.ProductNotFound:
        raise HTTPException(status_code=404, detail="Product not found")
