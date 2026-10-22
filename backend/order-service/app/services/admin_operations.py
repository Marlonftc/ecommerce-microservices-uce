"""Admin view of the pending operations, and reactivation of FAILED ones.

A PERMANENT failure (a contract or configuration error) must be fixed first:
reactivating it without fixing the cause just fails again.
"""
from datetime import UTC, datetime

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from app.models import PendingOperation
from app.services import executor


class OperationNotFound(Exception):
    pass


class OperationNotRetryable(Exception):
    def __init__(self, status: str, leased: bool):
        super().__init__(f"operation is {status}{' and claimed' if leased else ''}")
        self.status = status
        self.leased = leased


def list_operations(db: Session, status: str | None, kind: str | None, error_kind: str | None,
                    order_id, page: int, size: int):
    filters = []
    if status:
        filters.append(PendingOperation.status == status)
    if kind:
        filters.append(PendingOperation.kind == kind)
    if error_kind:
        filters.append(PendingOperation.error_kind == error_kind)
    if order_id:
        filters.append(PendingOperation.order_id == order_id)
    total = db.scalar(select(func.count()).select_from(PendingOperation).where(*filters))
    items = list(db.scalars(select(PendingOperation).where(*filters)
                            .order_by(PendingOperation.updated_at.desc(), PendingOperation.id.desc())
                            .limit(size).offset((page - 1) * size)))
    return items, total


def retry(db: Session, op_id: int) -> tuple[PendingOperation, str]:
    """FAILED and not claimed -> PENDING, attempts 0, due now, then one run."""
    now = datetime.now(UTC)
    reactivated = db.execute(
        update(PendingOperation)
        .where(PendingOperation.id == op_id, PendingOperation.status == "FAILED",
               or_(PendingOperation.locked_until.is_(None), PendingOperation.locked_until < now))
        .values(status="PENDING", attempts=0, next_attempt_at=now, error_kind=None, last_error=None,
                lease_token=None, locked_until=None)
        .returning(PendingOperation.id)
    ).one_or_none()
    db.commit()
    if reactivated is None:
        op = db.get(PendingOperation, op_id)
        db.commit()
        if op is None:
            raise OperationNotFound
        raise OperationNotRetryable(op.status, op.locked_until is not None and op.locked_until >= now)

    outcome = executor.run_operation(db, op_id, ignore_schedule=True)
    op = db.scalars(select(PendingOperation).where(PendingOperation.id == op_id)
                    .execution_options(populate_existing=True)).one()
    db.commit()
    return op, outcome
