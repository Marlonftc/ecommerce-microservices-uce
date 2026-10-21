import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from app.dependencies import CurrentUser, DbSession
from app.schemas import PaymentOut
from app.services import payment_service as ps

router = APIRouter(prefix="/payments", tags=["payments"])

AUTH = {401: {"description": "Missing, invalid or expired token"}}


@router.get("", response_model=list[PaymentOut], responses=AUTH)
def read_payments(
    db: DbSession,
    user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    """A customer sees only their own payments; an admin sees all."""
    return ps.list_for_user(db, user.id, user.is_admin, limit, offset)


@router.get("/{payment_id}", response_model=PaymentOut,
            responses={**AUTH, 404: {"description": "Payment not found"}})
def read_payment(payment_id: uuid.UUID, db: DbSession, user: CurrentUser):
    try:
        return ps.get_for_user(db, payment_id, user.id, user.is_admin)
    except ps.PaymentNotFound:
        # Same answer when it does not exist and when it belongs to someone else
        raise HTTPException(status_code=404, detail="Payment not found")
