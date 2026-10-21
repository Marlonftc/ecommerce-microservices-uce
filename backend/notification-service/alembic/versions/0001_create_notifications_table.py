"""create notifications table

Revision ID: 0001
Revises:
Create Date: 2026-10-21
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "notifications",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("event_id", sa.String(length=100), nullable=False, unique=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("type", sa.String(length=30), nullable=False),
        sa.Column("amount", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("message", sa.String(length=1000), nullable=False),
        sa.Column("is_read", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "type IN ('ORDER_PAID', 'PAYMENT_REJECTED', 'ORDER_CANCELLED')",
            name="ck_notifications_type",
        ),
        sa.CheckConstraint(
            "(amount IS NULL AND currency IS NULL)"
            " OR (amount IS NOT NULL AND currency IS NOT NULL"
            " AND amount > 0 AND currency = 'USD')",
            name="ck_notifications_amount_currency",
        ),
        sa.CheckConstraint("is_read = (read_at IS NOT NULL)", name="ck_notifications_read_at"),
    )
    op.create_index("ix_notifications_user_id_created_at", "notifications", ["user_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_notifications_user_id_created_at", table_name="notifications")
    op.drop_table("notifications")
