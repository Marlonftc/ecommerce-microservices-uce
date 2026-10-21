"""Pending operations (outbox): work that must reach another service.

They are always added in the SAME transaction as the order change that
requires them, so a crash can never leave a change without its follow-up.
Executing them comes in the next stages.
"""
import uuid
from typing import Any

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import PendingOperation


def operation_key(order_id: uuid.UUID, name: str) -> str:
    """"<order_id>:RELEASE_INVENTORY"; for notifications name is the event type."""
    return f"{order_id}:{name}"


def add_operation(db: Session, order_id: uuid.UUID, kind: str, name: str | None = None,
                  payload: dict[str, Any] | None = None) -> None:
    """Adds the operation once: the same key twice is ignored (idempotent)."""
    db.execute(insert(PendingOperation)
               .values(order_id=order_id, kind=kind, operation_key=operation_key(order_id, name or kind),
                       payload=payload or {})
               .on_conflict_do_nothing(index_elements=["operation_key"]))
