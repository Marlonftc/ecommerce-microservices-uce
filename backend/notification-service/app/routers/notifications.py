import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from app.dependencies import CurrentUser, DbSession
from app.schemas import NotificationOut, NotificationPage
from app.services import notification_service as ns

router = APIRouter(prefix="/notifications", tags=["notifications"])

AUTH = {401: {"description": "Missing, invalid or expired token"}}


@router.get("", response_model=NotificationPage, responses=AUTH)
def read_notifications(
    db: DbSession,
    user: CurrentUser,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
    unread_only: bool = False,
):
    """A customer sees only their own notifications; an admin sees all. Newest first."""
    items, total = ns.list_for_user(db, user.id, user.is_admin, unread_only, page, size)
    return NotificationPage(items=items, total=total, page=page, size=size)


@router.patch("/{notification_id}/read", response_model=NotificationOut,
              responses={**AUTH, 404: {"description": "Notification not found"}})
def mark_notification_as_read(notification_id: uuid.UUID, db: DbSession, user: CurrentUser):
    """Owner only, also for admins. Repeating it keeps the first read date."""
    try:
        return ns.mark_as_read(db, notification_id, user.id)
    except ns.NotificationNotFound:
        # Same answer when it does not exist and when it belongs to someone else
        raise HTTPException(status_code=404, detail="Notification not found")
