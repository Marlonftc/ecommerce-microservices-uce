"""The service stopped after saving PENDING and before resolving the payment."""
import uuid
from decimal import Decimal

from sqlalchemy import update

from app.models import Payment
from app.schemas import PaymentIn
from app.services import payment_service
from tests.conftest import pay, payment_body


def leave_pending(db_session, body, key, simulation="APPROVE"):
    """Simulates transaction A committed and the process dying before B."""
    db_session.add(Payment(idempotency_key=key, order_id=uuid.UUID(body["order_id"]),
                           user_id=body["user_id"], amount=Decimal(body["amount"]),
                           currency="USD", simulation=simulation, status="PENDING"))
    db_session.commit()


def test_pending_payment_is_completed_with_the_stored_parameters(client, db_session):
    body = payment_body(simulation="REJECT")
    leave_pending(db_session, body, "k-r2", simulation="REJECT")

    response = pay(client, body, key="k-r2")

    assert response.status_code == 200  # the original payment, not a new one
    assert response.json()["status"] == "REJECTED"
    assert response.json()["processed_at"] is not None


def test_pending_payment_cannot_be_resumed_with_different_data(client, db_session):
    body = payment_body(simulation="REJECT")
    leave_pending(db_session, body, "k-r3", simulation="REJECT")

    # Same key but asking for an approval: rejected, the stored request wins
    response = pay(client, {**body, "simulation": "APPROVE"}, key="k-r3")

    assert response.status_code == 409


def test_resolved_payments_are_final(db_session):
    body = payment_body(simulation="APPROVE")
    leave_pending(db_session, body, "k-r4")
    payment = payment_service.process_payment(
        db_session, PaymentIn(**body), "k-r4")[0]

    # Even forcing another simulation mode, a resolved payment does not change
    db_session.execute(update(Payment).where(Payment.id == payment.id).values(simulation="REJECT"))
    db_session.commit()
    again = payment_service._resolve(db_session, payment.id)

    assert again.status == "APPROVED"
