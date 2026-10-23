from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, status

from app.dependencies import AdminUser, CurrentUser, DbSession
from app.schemas import UserOut, UserUpdate
from app.services import user_service
from app.services.user_service import MAX_USER_ID

router = APIRouter(prefix="/users", tags=["users"])

NOT_AUTHENTICATED = {401: {"description": "Missing, invalid or expired token"}}
NOT_ADMIN = {403: {"description": "Admin role required"}}

MAX_IDS = 100
DEFAULT_LIMIT = 50


def _invalid(message: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=message)


def _conflict(code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail={"code": code, "message": message})


def parse_ids(raw: str) -> list[int]:
    """Parses "1,2,3": 1 to 100 values (counted before removing duplicates)."""
    parts = [part.strip() for part in raw.split(",")]
    if len(parts) > MAX_IDS:
        raise _invalid(f"ids must contain between 1 and {MAX_IDS} values")
    ids: list[int] = []
    for part in parts:
        # isdecimal + isascii: only 0-9, so "+1", "-1", "1.5" or "" are rejected
        if not (part.isascii() and part.isdecimal()):
            raise _invalid("ids must be comma separated positive integers")
        value = int(part)
        if not 1 <= value <= MAX_USER_ID:
            raise _invalid(f"each id must be between 1 and {MAX_USER_ID}")
        ids.append(value)
    return sorted(set(ids))


@router.get("/me", response_model=UserOut, responses=NOT_AUTHENTICATED)
def read_me(user: CurrentUser):
    return user


@router.get(
    "",
    response_model=list[UserOut],
    responses={**NOT_AUTHENTICATED, **NOT_ADMIN,
               422: {"description": "Invalid limit, offset or ids, or ids combined with limit/offset"}},
)
def read_users(
    _: AdminUser,
    db: DbSession,
    limit: Annotated[int | None, Query(ge=1, le=100, description=f"Page size, {DEFAULT_LIMIT} if omitted")] = None,
    offset: Annotated[int | None, Query(ge=0, description="Users to skip, 0 if omitted")] = None,
    ids: Annotated[str | None, Query(
        description=f"Comma separated user ids, for example 1,2,3 (1 to {MAX_IDS}). "
                    "Unknown ids are skipped. Cannot be combined with limit or offset.")] = None,
):
    """Without ids: paginated list ordered by id. With ids: those users ordered by id.

    Both forms return the same structure: a JSON array of UserOut.
    """
    if ids is not None:
        if limit is not None or offset is not None:
            raise _invalid("ids cannot be combined with limit or offset")
        return user_service.get_users_by_ids(db, parse_ids(ids))
    return user_service.list_users(db, limit if limit is not None else DEFAULT_LIMIT, offset or 0)


@router.patch(
    "/{user_id}",
    response_model=UserOut,
    responses={
        **NOT_AUTHENTICATED,
        403: {"description": "Admin role required (also if it was lost while the request waited)"},
        404: {"description": "User not found"},
        409: {"description": "CANNOT_MODIFY_SELF or LAST_ACTIVE_ADMIN"},
    },
)
def update_user(
    user_id: Annotated[int, Path(ge=1, le=MAX_USER_ID)],
    data: UserUpdate,
    admin: AdminUser,
    db: DbSession,
):
    """Changes role and/or is_active of another account. Users are never deleted."""
    try:
        return user_service.update_user(db, admin.id, user_id, data.model_dump(exclude_unset=True))
    except user_service.ActorNotAdmin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin role required")
    except user_service.CannotModifySelf:
        raise _conflict("CANNOT_MODIFY_SELF", "An administrator cannot modify their own account")
    except user_service.UserNotFound:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    except user_service.LastActiveAdmin:
        raise _conflict("LAST_ACTIVE_ADMIN", "At least one active administrator must remain")
