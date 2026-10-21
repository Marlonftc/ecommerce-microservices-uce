from collections.abc import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings

# The engine manages a pool of connections to PostgreSQL.
# pool_pre_ping checks each connection before using it, so the service
# recovers by itself after the database restarts.
engine = create_engine(
    settings.database_url,
    pool_size=5,
    max_overflow=5,
    pool_pre_ping=True,
    connect_args={"connect_timeout": 3},
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    """Parent class of all the tables of this service."""


def get_db() -> Iterator[Session]:
    """Gives one database session per request and always closes it."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def check_database() -> None:
    """Raises an exception if the database is not reachable."""
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
