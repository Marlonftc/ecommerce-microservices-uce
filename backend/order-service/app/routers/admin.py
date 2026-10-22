import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query

from app.dependencies import AdminUser, DbSession
from app.schemas import OperationOut, OperationPage, RetryOut
from app.services import admin_operations as ao

router = APIRouter(prefix="/admin/operations", tags=["admin"])

ERRORS = {401: {"description": "Missing, invalid or expired token"},
          403: {"description": "Admin role required"}}


@router.get("", response_model=OperationPage, responses=ERRORS)
def read_operations(
    db: DbSession,
    _: AdminUser,
    status: Literal["PENDING", "DONE", "FAILED"] | None = None,
    kind: Literal["RESOLVE_PAYMENT", "CONFIRM_INVENTORY", "RELEASE_INVENTORY", "SEND_NOTIFICATION"] | None = None,
    error_kind: Literal["TRANSIENT", "PERMANENT"] | None = None,
    order_id: uuid.UUID | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
):
    items, total = ao.list_operations(db, status, kind, error_kind, order_id, page, size)
    return OperationPage(items=items, total=total, page=page, size=size)


@router.post("/{operation_id}/retry", response_model=RetryOut,
             responses={**ERRORS, 404: {"description": "Operation not found"},
                        409: {"description": "OPERATION_NOT_RETRYABLE: not FAILED, or claimed right now"}})
def retry_operation(operation_id: int, db: DbSession, _: AdminUser):
    """Reactivates a FAILED operation and runs it once. Fix the cause of a
    PERMANENT failure first: repeating an invalid call does not fix it."""
    try:
        op, outcome = ao.retry(db, operation_id)
    except ao.OperationNotFound:
        raise HTTPException(status_code=404, detail="Operation not found")
    except ao.OperationNotRetryable as exc:
        raise HTTPException(status_code=409, detail={
            "code": "OPERATION_NOT_RETRYABLE", "message": "Only a FAILED operation that is not claimed",
            "status": exc.status, "claimed": exc.leased})
    return RetryOut(outcome=outcome, operation=OperationOut.model_validate(op))
