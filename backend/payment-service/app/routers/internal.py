import asyncio
import uuid
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Response, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from app.config import settings
from app.dependencies import DbSession
from app.internal_auth import InternalOnly
from app.schemas import PaymentIn, PaymentOut
from app.services import payment_service as ps

# Called only by order-service. Every route requires X-Internal-Key.
router = APIRouter(prefix="/internal/payments", tags=["internal"], dependencies=[InternalOnly])


def _conflict(code: str, message: str) -> HTTPException:
    return HTTPException(status_code=409, detail={"code": code, "message": message})


def _process(db: Session, data: PaymentIn, idempotency_key: str) -> tuple[PaymentOut, bool]:
    payment, created = ps.process_payment(db, data, idempotency_key)
    result = PaymentOut.model_validate(payment)
    # Reading the final values opened a new (read) transaction. Closing the
    # session ends it and returns the connection to the pool, so nothing is
    # held open during the SLOW_APPROVE wait.
    db.close()
    return result, created


@router.post(
    "",
    response_model=PaymentOut,
    status_code=status.HTTP_201_CREATED,
    responses={
        200: {"description": "Repeated request: the original payment is returned"},
        409: {"description": "Key reused with other data, or the order already has a payment"},
        503: {"description": "No payment provider configured (simulation disabled)"},
    },
)
async def create_payment(
    data: PaymentIn,
    db: DbSession,
    response: Response,
    idempotency_key: Annotated[str, Header(min_length=1, max_length=100)],
):
    if not settings.payment_simulation_enabled:
        # Checked before touching the database: no payment record is created
        raise HTTPException(status_code=503, detail="No payment provider configured")

    try:
        # The database work runs in a worker thread and both transactions are
        # committed before returning here.
        payment, created = await run_in_threadpool(_process, db, data, idempotency_key)
    except ps.IdempotencyKeyReused:
        raise _conflict("IDEMPOTENCY_KEY_REUSED", "Idempotency-Key already used with different data")
    except ps.OrderAlreadyHasPayment:
        raise _conflict("ORDER_ALREADY_HAS_PAYMENT", "This order already has a payment")

    if created and payment.simulation == "SLOW_APPROVE":
        # The APPROVED result is already committed. This wait holds no
        # transaction and no lock: it only delays the HTTP answer, so that
        # order-service times out and must reconcile.
        await asyncio.sleep(settings.payment_slow_delay_seconds)

    if not created:
        response.status_code = status.HTTP_200_OK
    return payment


@router.get("", response_model=PaymentOut, responses={404: {"description": "No payment for this order"}})
def read_payment_by_order(order_id: uuid.UUID, db: DbSession):
    """Used by the reconciliation of order-service after a timeout."""
    try:
        return ps.get_by_order(db, order_id)
    except ps.PaymentNotFound:
        raise HTTPException(status_code=404, detail="No payment for this order")
