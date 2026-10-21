from fastapi import APIRouter, Query

from app.dependencies import AdminUser, CurrentUser, DbSession
from app.schemas import UserOut
from app.services import user_service

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me", response_model=UserOut)
def read_me(user: CurrentUser):
    return user


@router.get("", response_model=list[UserOut])
def read_users(
    _: AdminUser,
    db: DbSession,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    return user_service.list_users(db, limit, offset)
