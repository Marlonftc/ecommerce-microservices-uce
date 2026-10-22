"""create order tables

Five tables: carts, cart_items, orders, order_items and pending_operations.
Foreign keys only between these tables; user_id and product_id belong to
other services and are plain integers.

Revision ID: 0001
Revises:
Create Date: 2026-10-21
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0001'
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('carts',
    sa.Column('user_id', sa.Integer(), autoincrement=False, nullable=False),
    sa.Column('version', sa.Integer(), server_default='0', nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('user_id > 0', name='ck_carts_user_id_positive'),
    sa.CheckConstraint('version >= 0', name='ck_carts_version_not_negative'),
    sa.PrimaryKeyConstraint('user_id')
    )
    op.create_table('orders',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('currency', sa.String(length=3), server_default='USD', nullable=False),
    sa.Column('total', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('checkout_key', sa.String(length=100), nullable=False),
    sa.Column('cart_version', sa.Integer(), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('cancel_reason', sa.String(length=30), nullable=True),
    sa.Column('inventory_status', sa.String(length=20), server_default='PENDING', nullable=False),
    sa.Column('payment_key', sa.String(length=100), nullable=True),
    sa.Column('payment_simulation', sa.String(length=20), nullable=True),
    sa.Column('payment_status', sa.String(length=20), nullable=True),
    sa.Column('payment_id', sa.Uuid(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("(status = 'CANCELLED') = (cancel_reason IS NOT NULL)", name='ck_orders_cancel_reason_only_when_cancelled'),
    sa.CheckConstraint("cancel_reason IN ('USER', 'EXPIRED', 'INSUFFICIENT_STOCK', 'RESERVATION_TIMEOUT')", name='ck_orders_cancel_reason'),
    sa.CheckConstraint("currency = 'USD'", name='ck_orders_currency_usd'),
    sa.CheckConstraint("inventory_status IN ('PENDING', 'RESERVED', 'CONFIRMED', 'RELEASED', 'NOT_RESERVED')", name='ck_orders_inventory_status'),
    sa.CheckConstraint("payment_simulation IN ('APPROVE', 'REJECT', 'SLOW_APPROVE')", name='ck_orders_payment_simulation'),
    sa.CheckConstraint("payment_status IN ('REQUESTED', 'APPROVED', 'REJECTED')", name='ck_orders_payment_status'),
    sa.CheckConstraint("status <> 'PAID' OR COALESCE(payment_status, '') = 'APPROVED'", name='ck_orders_paid_is_approved'),
    sa.CheckConstraint("status <> 'PAYMENT_REJECTED' OR COALESCE(payment_status, '') = 'REJECTED'", name='ck_orders_rejected_is_rejected'),
    sa.CheckConstraint("status <> 'PENDING_PAYMENT' OR expires_at IS NOT NULL", name='ck_orders_expires_at_when_pending_payment'),
    sa.CheckConstraint("status IN ('RESERVING', 'PENDING_PAYMENT', 'PAYMENT_PENDING', 'PAID', 'PAYMENT_REJECTED', 'CANCELLED')", name='ck_orders_status'),
    sa.CheckConstraint("status NOT IN ('PAYMENT_PENDING', 'PAID', 'PAYMENT_REJECTED') OR payment_key IS NOT NULL", name='ck_orders_payment_intent_recorded'),
    sa.CheckConstraint('(payment_key IS NULL) = (payment_simulation IS NULL) AND (payment_key IS NULL) = (payment_status IS NULL)', name='ck_orders_payment_intent_complete'),
    sa.CheckConstraint('cart_version >= 0', name='ck_orders_cart_version_not_negative'),
    sa.CheckConstraint('total > 0', name='ck_orders_total_positive'),
    sa.CheckConstraint('user_id > 0', name='ck_orders_user_id_positive'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_orders_status_updated_at', 'orders', ['status', 'updated_at'], unique=False)
    op.create_index('ix_orders_user_id_created_at', 'orders', ['user_id', 'created_at'], unique=False)
    op.create_index('uq_orders_one_active_per_user', 'orders', ['user_id'], unique=True, postgresql_where=sa.text("status IN ('RESERVING', 'PENDING_PAYMENT', 'PAYMENT_PENDING')"))
    op.create_index('uq_orders_user_id_checkout_key', 'orders', ['user_id', 'checkout_key'], unique=True)
    op.create_table('cart_items',
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('product_id', sa.Integer(), autoincrement=False, nullable=False),
    sa.Column('quantity', sa.Integer(), nullable=False),
    sa.CheckConstraint('product_id > 0', name='ck_cart_items_product_id_positive'),
    sa.CheckConstraint('quantity BETWEEN 1 AND 99', name='ck_cart_items_quantity_range'),
    sa.ForeignKeyConstraint(['user_id'], ['carts.user_id'], name='fk_cart_items_user_id_carts', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('user_id', 'product_id')
    )
    op.create_table('order_items',
    sa.Column('order_id', sa.Uuid(), nullable=False),
    sa.Column('product_id', sa.Integer(), autoincrement=False, nullable=False),
    sa.Column('sku', sa.String(length=32), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('unit_price', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('quantity', sa.Integer(), nullable=False),
    sa.Column('subtotal', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.CheckConstraint('product_id > 0', name='ck_order_items_product_id_positive'),
    sa.CheckConstraint('quantity BETWEEN 1 AND 99', name='ck_order_items_quantity_range'),
    sa.CheckConstraint('subtotal = unit_price * quantity', name='ck_order_items_subtotal'),
    sa.CheckConstraint('unit_price > 0', name='ck_order_items_unit_price_positive'),
    sa.ForeignKeyConstraint(['order_id'], ['orders.id'], name='fk_order_items_order_id_orders', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('order_id', 'product_id')
    )
    op.create_table('pending_operations',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('order_id', sa.Uuid(), nullable=False),
    sa.Column('kind', sa.String(length=30), nullable=False),
    sa.Column('operation_key', sa.String(length=150), nullable=False),
    sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('status', sa.String(length=20), server_default='PENDING', nullable=False),
    sa.Column('attempts', sa.Integer(), server_default='0', nullable=False),
    sa.Column('next_attempt_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('last_error', sa.String(length=500), nullable=True),
    sa.Column('error_kind', sa.String(length=20), nullable=True),
    sa.Column('locked_until', sa.DateTime(timezone=True), nullable=True),
    sa.Column('lease_token', sa.Uuid(), nullable=True),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("(status = 'DONE') = (completed_at IS NOT NULL)", name='ck_pending_operations_completed_at_when_done'),
    sa.CheckConstraint("error_kind IN ('TRANSIENT', 'PERMANENT')", name='ck_pending_operations_error_kind'),
    sa.CheckConstraint("kind IN ('RESOLVE_PAYMENT', 'CONFIRM_INVENTORY', 'RELEASE_INVENTORY', 'SEND_NOTIFICATION')", name='ck_pending_operations_kind'),
    sa.CheckConstraint("status IN ('PENDING', 'DONE', 'FAILED')", name='ck_pending_operations_status'),
    sa.CheckConstraint('(locked_until IS NULL) = (lease_token IS NULL)', name='ck_pending_operations_lease_complete'),
    sa.CheckConstraint('attempts >= 0', name='ck_pending_operations_attempts_not_negative'),
    sa.ForeignKeyConstraint(['order_id'], ['orders.id'], name='fk_pending_operations_order_id_orders', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('operation_key', name='uq_pending_operations_operation_key')
    )
    op.create_index('ix_pending_operations_due', 'pending_operations', ['next_attempt_at'], unique=False, postgresql_where=sa.text("status <> 'DONE'"))
    op.create_index('ix_pending_operations_order_id', 'pending_operations', ['order_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_pending_operations_order_id', table_name='pending_operations')
    op.drop_index('ix_pending_operations_due', table_name='pending_operations', postgresql_where=sa.text("status <> 'DONE'"))
    op.drop_table('pending_operations')
    op.drop_table('order_items')
    op.drop_table('cart_items')
    op.drop_index('uq_orders_user_id_checkout_key', table_name='orders')
    op.drop_index('uq_orders_one_active_per_user', table_name='orders', postgresql_where=sa.text("status IN ('RESERVING', 'PENDING_PAYMENT', 'PAYMENT_PENDING')"))
    op.drop_index('ix_orders_user_id_created_at', table_name='orders')
    op.drop_index('ix_orders_status_updated_at', table_name='orders')
    op.drop_table('orders')
    op.drop_table('carts')
