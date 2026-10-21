"""Reservations of units for orders. Every operation is idempotent by order_id.

States: RESERVED -> CONFIRMED (the sale happened)
        RESERVED -> RELEASED  (units go back to available)
A release of an unknown order creates an empty RELEASED reservation
("tombstone") so that a late reservation for that order is rejected.
"""
import uuid

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.models import Reservation, ReservationItem, Stock
from app.schemas import ReservationItemIn


class ReservationNotFound(Exception):
    pass


class ReservationReleased(Exception):
    """The order was released (or cancelled before reserving)."""


class ReservationMismatch(Exception):
    """A reservation for this order exists with different products or quantities."""


class InvalidTransition(Exception):
    pass


class InsufficientStock(Exception):
    def __init__(self, product_id: int):
        super().__init__(product_id)
        self.product_id = product_id


def _locked(db: Session, order_id: uuid.UUID) -> Reservation | None:
    """Reads the reservation and locks its row until the transaction ends."""
    return db.scalar(
        select(Reservation)
        .where(Reservation.order_id == order_id)
        .options(selectinload(Reservation.items))
        .with_for_update()
    )


def _same_content(reservation: Reservation, items: list[ReservationItemIn]) -> bool:
    stored = sorted((i.product_id, i.quantity) for i in reservation.items)
    requested = sorted((i.product_id, i.quantity) for i in items)
    return stored == requested


def get_reservation(db: Session, order_id: uuid.UUID) -> Reservation:
    reservation = db.scalar(
        select(Reservation).where(Reservation.order_id == order_id).options(selectinload(Reservation.items))
    )
    if reservation is None:
        raise ReservationNotFound
    return reservation


def reserve(db: Session, order_id: uuid.UUID, items: list[ReservationItemIn]) -> tuple[Reservation, bool]:
    """Reserves all the items or none. Returns (reservation, True if created now)."""
    for _ in range(2):  # second round only if another request created it at the same time
        existing = _locked(db, order_id)
        if existing is not None:
            db.commit()
            if existing.status == "RELEASED":
                raise ReservationReleased
            if not _same_content(existing, items):
                raise ReservationMismatch
            return existing, False

        try:
            # Always in the same order (by product id): two orders with the
            # same products lock the rows in the same order and cannot
            # block each other (no deadlock).
            for item in sorted(items, key=lambda i: i.product_id):
                # Conditional subtraction in one statement: only if there are
                # enough units. PostgreSQL locks the row, so two requests for
                # the last unit cannot both succeed.
                updated = db.scalar(
                    update(Stock)
                    .where(Stock.product_id == item.product_id, Stock.available >= item.quantity)
                    .values(available=Stock.available - item.quantity,
                            reserved=Stock.reserved + item.quantity)
                    .returning(Stock.product_id)
                )
                if updated is None:
                    db.rollback()  # undo the items already reserved: all or nothing
                    raise InsufficientStock(item.product_id)

            reservation = Reservation(
                order_id=order_id,
                status="RESERVED",
                items=[ReservationItem(product_id=i.product_id, quantity=i.quantity) for i in items],
            )
            db.add(reservation)
            db.commit()
            return reservation, True
        except IntegrityError:
            # Same order_id inserted by a simultaneous request: undo and read it
            db.rollback()
    raise RuntimeError("reservation could not be created")


def confirm(db: Session, order_id: uuid.UUID) -> Reservation:
    """The sale happened: the reserved units leave the warehouse."""
    reservation = _locked(db, order_id)
    if reservation is None:
        db.rollback()
        raise ReservationNotFound
    if reservation.status == "CONFIRMED":
        db.commit()
        return reservation
    if reservation.status == "RELEASED":
        db.rollback()
        raise InvalidTransition("a released reservation cannot be confirmed")

    for item in sorted(reservation.items, key=lambda i: i.product_id):
        db.execute(
            update(Stock)
            .where(Stock.product_id == item.product_id)
            .values(reserved=Stock.reserved - item.quantity)
        )
    reservation.status = "CONFIRMED"
    db.commit()
    return reservation


def release(db: Session, order_id: uuid.UUID) -> Reservation:
    """Gives the units back. Safe to repeat. Unknown order: creates a tombstone."""
    for _ in range(2):
        reservation = _locked(db, order_id)
        if reservation is None:
            try:
                tombstone = Reservation(order_id=order_id, status="RELEASED", items=[])
                db.add(tombstone)
                db.commit()
                return tombstone
            except IntegrityError:
                db.rollback()  # a reservation was created at the same time: read it again
                continue

        if reservation.status == "RELEASED":
            db.commit()
            return reservation
        if reservation.status == "CONFIRMED":
            db.rollback()
            raise InvalidTransition("a confirmed reservation cannot be released")

        for item in sorted(reservation.items, key=lambda i: i.product_id):
            db.execute(
                update(Stock)
                .where(Stock.product_id == item.product_id)
                .values(available=Stock.available + item.quantity,
                        reserved=Stock.reserved - item.quantity)
            )
        reservation.status = "RELEASED"
        db.commit()
        return reservation
    raise RuntimeError("reservation could not be released")
