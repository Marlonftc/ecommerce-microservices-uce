from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import User
from app.schemas import RegisterRequest
from app.security import hash_password, verify_password

# Hash used when the email does not exist, so a failed login takes the same
# time whether the user exists or not (avoids discovering registered emails).
_DUMMY_HASH = hash_password("dummy-password-for-timing")


class EmailAlreadyRegistered(Exception):
    pass


class InvalidCredentials(Exception):
    pass


def normalize_email(email: str) -> str:
    return email.strip().lower()


def register_user(db: Session, data: RegisterRequest) -> User:
    user = User(
        email=normalize_email(data.email),
        full_name=data.full_name,
        password_hash=hash_password(data.password),
        role="customer",  # a user can never choose its own role
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError as exc:
        # The unique index on email protects against two simultaneous registrations
        db.rollback()
        raise EmailAlreadyRegistered from exc
    db.refresh(user)
    return user


def authenticate(db: Session, email: str, password: str) -> User:
    user = db.scalar(select(User).where(User.email == normalize_email(email)))
    if user is None:
        verify_password(password, _DUMMY_HASH)
        raise InvalidCredentials
    if not verify_password(password, user.password_hash) or not user.is_active:
        raise InvalidCredentials
    return user


def list_users(db: Session, limit: int, offset: int) -> list[User]:
    return list(db.scalars(select(User).order_by(User.id).limit(limit).offset(offset)))


# --- administration of accounts ----------------------------------------------

# Fixed key of the PostgreSQL advisory lock that serializes every change of
# role or is_active. It is only used by this operation.
ROLE_CHANGE_LOCK_KEY = 7_105_001

# Largest value of the users.id column (PostgreSQL INTEGER)
MAX_USER_ID = 2_147_483_647


class UserNotFound(Exception):
    pass


class CannotModifySelf(Exception):
    pass


class ActorNotAdmin(Exception):
    """The administrator lost the role or was deactivated while the request waited."""


class LastActiveAdmin(Exception):
    pass


def get_users_by_ids(db: Session, ids: list[int]) -> list[User]:
    """Users whose id is in the list, ordered by id. Unknown ids are skipped."""
    return list(db.scalars(select(User).where(User.id.in_(ids)).order_by(User.id)))


def _ensure_admin_remains(db: Session, losing_admin_id: int) -> None:
    """Raises LastActiveAdmin if no other active administrator would remain."""
    others = db.scalar(
        select(func.count()).select_from(User)
        .where(User.role == "admin", User.is_active.is_(True), User.id != losing_admin_id)
    )
    if not others:
        raise LastActiveAdmin


def update_user(db: Session, actor_id: int, target_id: int, changes: dict) -> User:
    """Changes role and/or is_active of another account in one transaction.

    1. The advisory lock makes concurrent changes run one after another.
    2. With the lock held, the actor is read again from PostgreSQL: if it was
       deactivated or demoted while waiting, the change is refused.
    3. The target row is locked with FOR UPDATE and the change is applied
       only if at least one active administrator remains.
    COMMIT or ROLLBACK releases both locks.
    """
    try:
        db.execute(select(func.pg_advisory_xact_lock(ROLE_CHANGE_LOCK_KEY)))

        # populate_existing: ignore the copy already loaded by get_current_user
        actor = db.scalar(
            select(User).where(User.id == actor_id).execution_options(populate_existing=True)
        )
        if actor is None or not actor.is_active or actor.role != "admin":
            raise ActorNotAdmin
        if target_id == actor_id:
            raise CannotModifySelf

        target = db.scalar(
            select(User).where(User.id == target_id).with_for_update()
            .execution_options(populate_existing=True)
        )
        if target is None:
            raise UserNotFound

        new_role = changes.get("role", target.role)
        new_active = changes.get("is_active", target.is_active)
        was_active_admin = target.role == "admin" and target.is_active
        stays_active_admin = new_role == "admin" and new_active
        if was_active_admin and not stays_active_admin:
            _ensure_admin_remains(db, target.id)

        target.role = new_role
        target.is_active = new_active
        db.commit()
    except Exception:
        db.rollback()
        raise
    return target
