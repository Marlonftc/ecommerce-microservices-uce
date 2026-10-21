import uuid

from fastapi import APIRouter, HTTPException, Response, status

from app.dependencies import DbSession
from app.internal_auth import InternalOnly
from app.schemas import ReservationIn, ReservationOut
from app.services import reservation_service as rs

# Called only by order-service. Every route requires X-Internal-Key.
router = APIRouter(prefix="/internal/reservations", tags=["internal"], dependencies=[InternalOnly])


def _conflict(code: str, message: str, **extra) -> HTTPException:
    # A code in the body lets order-service tell the cases apart
    return HTTPException(status_code=409, detail={"code": code, "message": message, **extra})


@router.post("", response_model=ReservationOut, status_code=status.HTTP_201_CREATED,
             responses={200: {"description": "Repeated request: same reservation"},
                        409: {"description": "Insufficient stock, released order or different content"}})
def create_reservation(data: ReservationIn, db: DbSession, response: Response):
    try:
        reservation, created = rs.reserve(db, data.order_id, data.items)
    except rs.InsufficientStock as exc:
        raise _conflict("INSUFFICIENT_STOCK", "Not enough units", product_id=exc.product_id)
    except rs.ReservationReleased:
        raise _conflict("RESERVATION_RELEASED", "This order was released and cannot be reserved")
    except rs.ReservationMismatch:
        raise _conflict("RESERVATION_MISMATCH", "A reservation with different content exists")
    if not created:
        response.status_code = status.HTTP_200_OK
    return reservation


@router.get("/{order_id}", response_model=ReservationOut)
def read_reservation(order_id: uuid.UUID, db: DbSession):
    try:
        return rs.get_reservation(db, order_id)
    except rs.ReservationNotFound:
        raise HTTPException(status_code=404, detail="Reservation not found")


@router.post("/{order_id}/confirm", response_model=ReservationOut)
def confirm_reservation(order_id: uuid.UUID, db: DbSession):
    try:
        return rs.confirm(db, order_id)
    except rs.ReservationNotFound:
        raise HTTPException(status_code=404, detail="Reservation not found")
    except rs.InvalidTransition as exc:
        raise _conflict("INVALID_TRANSITION", str(exc))


@router.post("/{order_id}/release", response_model=ReservationOut)
def release_reservation(order_id: uuid.UUID, db: DbSession):
    try:
        return rs.release(db, order_id)
    except rs.InvalidTransition as exc:
        raise _conflict("INVALID_TRANSITION", str(exc))
