"""create inventory tables

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
        "stock",
        sa.Column("product_id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("available", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reserved", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("available >= 0", name="ck_stock_available_not_negative"),
        sa.CheckConstraint("reserved >= 0", name="ck_stock_reserved_not_negative"),
    )

    op.create_table(
        "reservations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("status IN ('RESERVED', 'CONFIRMED', 'RELEASED')", name="ck_reservations_status"),
    )
    op.create_index("ix_reservations_order_id", "reservations", ["order_id"], unique=True)

    op.create_table(
        "reservation_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("reservation_id", sa.Integer(),
                  sa.ForeignKey("reservations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.CheckConstraint("quantity > 0", name="ck_reservation_items_quantity_positive"),
        sa.UniqueConstraint("reservation_id", "product_id", name="uq_reservation_items_product"),
    )

    op.create_table(
        "stock_adjustments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("idempotency_key", sa.String(length=100), nullable=False, unique=True),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("delta", sa.Integer(), nullable=False),
        sa.Column("available_before", sa.Integer(), nullable=False),
        sa.Column("available_after", sa.Integer(), nullable=False),
        sa.Column("admin_user_id", sa.Integer(), nullable=True),
        sa.Column("reason", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("delta <> 0", name="ck_stock_adjustments_delta_not_zero"),
    )
    op.create_index("ix_stock_adjustments_product_id", "stock_adjustments", ["product_id"])


def downgrade() -> None:
    op.drop_index("ix_stock_adjustments_product_id", table_name="stock_adjustments")
    op.drop_table("stock_adjustments")
    op.drop_table("reservation_items")
    op.drop_index("ix_reservations_order_id", table_name="reservations")
    op.drop_table("reservations")
    op.drop_table("stock")
