"""Creates the initial administrator. Safe to run many times."""
import logging
import sys

from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy import select

from app.config import settings
from app.database import SessionLocal
from app.models import User
from app.security import hash_password

logging.basicConfig(level=settings.log_level)
logger = logging.getLogger("seed")


def create_admin() -> None:
    if not settings.admin_email or not settings.admin_password:
        logger.info("ADMIN_EMAIL or ADMIN_PASSWORD not set, skipping admin creation")
        return

    email = settings.admin_email.strip().lower()
    try:
        # Same rule as /auth/register: no account that could not register
        TypeAdapter(EmailStr).validate_python(email)
    except ValidationError:
        logger.error("ADMIN_EMAIL %r is not a valid email address", email)
        sys.exit(1)

    with SessionLocal() as db:
        if db.scalar(select(User).where(User.email == email)):
            logger.info("admin %s already exists", email)
            return

        db.add(User(
            email=email,
            full_name=settings.admin_name,
            password_hash=hash_password(settings.admin_password),
            role="admin",
        ))
        db.commit()
        logger.info("admin %s created", email)


if __name__ == "__main__":
    create_admin()
