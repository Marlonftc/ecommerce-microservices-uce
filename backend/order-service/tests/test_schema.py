"""Reads the PostgreSQL catalog: the constraints and indexes really exist.

alembic check only compares the model with the database; this test proves
that the CHECK constraints and the partial indexes are in PostgreSQL.
"""
import pytest
from sqlalchemy import text

TABLES = {"carts", "cart_items", "orders", "order_items", "pending_operations"}

EXPECTED_CHECKS = {
    "carts": {"ck_carts_user_id_positive", "ck_carts_version_not_negative"},
    "cart_items": {"ck_cart_items_product_id_positive", "ck_cart_items_quantity_range"},
    "orders": {
        "ck_orders_user_id_positive", "ck_orders_status", "ck_orders_currency_usd", "ck_orders_total_positive",
        "ck_orders_cart_version_not_negative", "ck_orders_inventory_status", "ck_orders_cancel_reason",
        "ck_orders_cancel_reason_only_when_cancelled", "ck_orders_expires_at_when_pending_payment",
        "ck_orders_payment_status", "ck_orders_payment_simulation", "ck_orders_payment_intent_complete",
        "ck_orders_payment_intent_recorded", "ck_orders_paid_is_approved", "ck_orders_rejected_is_rejected",
    },
    "order_items": {"ck_order_items_product_id_positive", "ck_order_items_quantity_range",
                    "ck_order_items_unit_price_positive", "ck_order_items_subtotal"},
    "pending_operations": {
        "ck_pending_operations_kind", "ck_pending_operations_status",
        "ck_pending_operations_attempts_not_negative", "ck_pending_operations_error_kind",
        "ck_pending_operations_completed_at_when_done", "ck_pending_operations_lease_complete",
    },
}


def constraints(db, kind):
    rows = db.execute(text(
        "SELECT rel.relname, con.conname FROM pg_constraint con "
        "JOIN pg_class rel ON rel.oid = con.conrelid "
        "JOIN pg_namespace ns ON ns.oid = rel.relnamespace "
        "WHERE ns.nspname = 'public' AND con.contype = :kind"), {"kind": kind})
    found: dict[str, set[str]] = {}
    for table, name in rows:
        found.setdefault(table, set()).add(name)
    return found


def test_the_five_tables_exist(db_session):
    names = set(db_session.execute(text(
        "SELECT tablename FROM pg_tables WHERE schemaname = 'public'")).scalars())

    assert TABLES <= names


def test_all_check_constraints_exist(db_session):
    found = constraints(db_session, "c")

    for table, expected in EXPECTED_CHECKS.items():
        assert found.get(table, set()) == expected, table


def test_internal_foreign_keys_only(db_session):
    rows = set(db_session.execute(text(
        "SELECT con.conname, src.relname, dst.relname FROM pg_constraint con "
        "JOIN pg_class src ON src.oid = con.conrelid JOIN pg_class dst ON dst.oid = con.confrelid "
        "WHERE con.contype = 'f'")).all())

    assert rows == {
        ("fk_cart_items_user_id_carts", "cart_items", "carts"),
        ("fk_order_items_order_id_orders", "order_items", "orders"),
        ("fk_pending_operations_order_id_orders", "pending_operations", "orders"),
    }


def test_unique_constraints_and_indexes(db_session):
    indexes = dict(db_session.execute(text(
        "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = 'public'")).all())

    assert "UNIQUE" in indexes["uq_orders_user_id_checkout_key"]
    assert "(user_id, checkout_key)" in indexes["uq_orders_user_id_checkout_key"]
    assert "UNIQUE" in indexes["uq_pending_operations_operation_key"]
    assert "(user_id, created_at)" in indexes["ix_orders_user_id_created_at"]
    assert "(order_id)" in indexes["ix_pending_operations_order_id"]


def test_partial_index_one_active_order_per_user(db_session):
    definition = db_session.scalar(text(
        "SELECT indexdef FROM pg_indexes WHERE indexname = 'uq_orders_one_active_per_user'"))

    assert definition is not None
    assert "UNIQUE" in definition and "(user_id)" in definition
    assert "WHERE" in definition
    for status in ("RESERVING", "PENDING_PAYMENT", "PAYMENT_PENDING"):
        assert status in definition
    for status in ("PAID", "PAYMENT_REJECTED", "CANCELLED"):
        assert status not in definition


def test_partial_index_of_pending_operations(db_session):
    definition = db_session.scalar(text(
        "SELECT indexdef FROM pg_indexes WHERE indexname = 'ix_pending_operations_due'"))

    assert "(next_attempt_at)" in definition and "WHERE" in definition and "DONE" in definition


@pytest.mark.parametrize("table, column, expected", [
    ("orders", "total", "numeric(12,2)"),
    ("order_items", "unit_price", "numeric(12,2)"),
    ("order_items", "subtotal", "numeric(12,2)"),
    ("orders", "created_at", "timestamp with time zone"),
    ("orders", "expires_at", "timestamp with time zone"),
    ("pending_operations", "payload", "jsonb"),
    ("orders", "user_id", "integer"),
    ("order_items", "product_id", "integer"),
    ("orders", "id", "uuid"),
])
def test_column_types(db_session, table, column, expected):
    found = db_session.scalar(text(
        "SELECT format_type(a.atttypid, a.atttypmod) FROM pg_attribute a "
        "JOIN pg_class c ON c.oid = a.attrelid WHERE c.relname = :t AND a.attname = :c"),
        {"t": table, "c": column})

    assert found == expected
