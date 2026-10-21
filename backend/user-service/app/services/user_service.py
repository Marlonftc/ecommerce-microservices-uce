from sqlalchemy import select
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
