from fastapi import APIRouter, HTTPException, Response, status

from app.dependencies import DbSession
from app.internal_auth import InternalOnly
from app.schemas import NotificationEventIn, NotificationOut
from app.services import notification_service as ns

# Called only by order-service. Every route requires X-Internal-Key.
router = APIRouter(prefix="/internal/notifications", tags=["internal"], dependencies=[InternalOnly])


@router.post(
    "",
    response_model=NotificationOut,
    status_code=status.HTTP_201_CREATED,
    responses={
        200: {"description": "Repeated event: the original notification is returned unchanged"},
        403: {"description": "Missing or wrong X-Internal-Key"},
        409: {"description": "event_id already used with different data"},
    },
)
def register_notification(event: NotificationEventIn, db: DbSession, response: Response):
    try:
        notification, created = ns.register_event(db, event)
    except ns.EventConflict:
        raise HTTPException(status_code=409, detail={
            "code": "EVENT_CONFLICT", "message": "event_id already used with different data"})
    if not created:
        response.status_code = status.HTTP_200_OK
    return notification
