"""Registering, listing and reading notifications."""
import uuid

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.messages import build_message
from app.models import Notification
from app.schemas import NotificationEventIn


class EventConflict(Exception):
    """The event_id already exists with different business data."""


class NotificationNotFound(Exception):
    pass


def _business_data(n: Notification | NotificationEventIn) -> tuple:
    # Only what order-service sends. Texts, dates and read state are excluded.
    return (n.event_id, n.user_id, n.order_id, n.type, n.amount, n.currency)


def register_event(db: Session, event: NotificationEventIn) -> tuple[Notification, bool]:
    """Returns (notification, True if it was created by this call).

    INSERT ... ON CONFLICT (event_id) DO NOTHING: if another request already
    inserted the same event_id, even at this very moment, PostgreSQL waits for
    it and inserts nothing. No IntegrityError, so no error 500.
    """
    title, message = build_message(event)
    new_id = db.scalar(
        insert(Notification)
        .values(id=uuid.uuid4(), title=title, message=message, **event.model_dump())
        .on_conflict_do_nothing(index_elements=["event_id"])
        .returning(Notification.id)
    )
    db.commit()

    if new_id is not None:
        return db.get(Notification, new_id), True

    existing = db.scalar(select(Notification).where(Notification.event_id == event.event_id))
    # Decimal("149.9") == Decimal("149.90"): equal amounts compare as equal
    if _business_data(existing) != _business_data(event):
        raise EventConflict
    return existing, False


def list_for_user(db: Session, user_id: int, is_admin: bool, unread_only: bool,
                  page: int, size: int) -> tuple[list[Notification], int]:
    filters = []
    if not is_admin:
        # Owner filter inside the SQL: other users' rows are never loaded
        filters.append(Notification.user_id == user_id)
    if unread_only:
        filters.append(Notification.is_read.is_(False))

    total = db.scalar(select(func.count()).select_from(Notification).where(*filters))
    items = db.scalars(
        select(Notification)
        .where(*filters)
        # id breaks ties between rows created at the same instant: pages never repeat or skip rows
        .order_by(Notification.created_at.desc(), Notification.id.desc())
        .limit(size)
        .offset((page - 1) * size)
    )
    return list(items), total


def mark_as_read(db: Session, notification_id: uuid.UUID, user_id: int) -> Notification:
    """Only the owner. Marking it again keeps the original read date."""
    db.execute(
        update(Notification)
        .where(
            Notification.id == notification_id,
            Notification.user_id == user_id,
            Notification.read_at.is_(None),  # already read: nothing changes
        )
        # Both columns change together, as the CHECK of the table requires
        .values(is_read=True, read_at=func.now())
    )
    db.commit()

    notification = db.scalar(
        select(Notification).where(Notification.id == notification_id, Notification.user_id == user_id)
    )
    if notification is None:
        raise NotificationNotFound
    return notification
