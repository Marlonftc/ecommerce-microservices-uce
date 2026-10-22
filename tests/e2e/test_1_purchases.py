"""Scenarios 1-6: purchases between the six real services (no mocks)."""
import threading
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

from support import (cancel, cart, checkout, new_customer, new_product, notifications, operations, order,
                     pay, payments, stock, wait_until)


# 1 ------------------------------------------------------------------------------------------
def test_1_approved_purchase(adm):
    customer = new_customer()
    product = new_product(adm, stock=5, price="25.50")

    placed = checkout(customer, product, quantity=2)
    assert placed.status_code == 201
    o = placed.json()
    assert (o["status"], o["inventory_status"], o["total"]) == ("PENDING_PAYMENT", "RESERVED", "51.00")
    assert stock(adm, product) == (3, 2)               # the reservation lowers available

    paid = pay(customer, o["id"])

    assert paid.status_code == 200
    assert (paid.json()["status"], paid.json()["inventory_status"]) == ("PAID", "CONFIRMED")
    assert stock(adm, product) == (3, 0)               # confirm lowers reserved, NOT available again
    assert len(notifications(customer, "ORDER_PAID", o["id"])) == 1
    assert [(p["status"], p["amount"]) for p in payments(customer)] == [("APPROVED", "51.00")]
    assert cart(customer)["items"] == []
    assert {k: op["status"] for k, op in operations(adm, o["id"]).items()} == {
        "RESOLVE_PAYMENT": "DONE", "CONFIRM_INVENTORY": "DONE", "ORDER_PAID": "DONE"}


# 2 ------------------------------------------------------------------------------------------
def test_2_rejected_payment(adm):
    customer = new_customer()
    product = new_product(adm, stock=5)
    o = checkout(customer, product, quantity=2).json()

    rejected = pay(customer, o["id"], "REJECT")

    assert rejected.status_code == 200
    assert (rejected.json()["status"], rejected.json()["inventory_status"]) == ("PAYMENT_REJECTED", "RELEASED")
    assert stock(adm, product) == (5, 0)               # reservation released
    assert len(notifications(customer, "PAYMENT_REJECTED", o["id"])) == 1
    assert [p["status"] for p in payments(customer)] == ["REJECTED"]
    kept = cart(customer)
    assert ([(i["product_id"], i["quantity"]) for i in kept["items"]], kept["locked"]) == ([(product, 2)], False)


# 3 ------------------------------------------------------------------------------------------
def test_3_slow_approve_resolved_by_the_worker(adm):
    customer = new_customer()
    product = new_product(adm, stock=3)
    o = checkout(customer, product).json()

    slow = pay(customer, o["id"], "SLOW_APPROVE")

    assert slow.status_code == 202
    assert (slow.json()["status"], slow.json()["payment_status"]) == ("PAYMENT_PENDING", "REQUESTED")
    assert [p["status"] for p in payments(customer)] == ["APPROVED"]   # approved over there already

    # No retry from the customer: the real worker reconciles it
    final = wait_until(lambda: order(customer, o["id"])["status"] == "PAID" and order(customer, o["id"]),
                       timeout=90, what="PAID by the worker")
    assert final["inventory_status"] in ("RESERVED", "CONFIRMED")
    wait_until(lambda: notifications(customer, "ORDER_PAID", o["id"]), timeout=60, what="ORDER_PAID")
    assert len(payments(customer)) == 1                 # the same payment, not a second one
    assert operations(adm, o["id"])["RESOLVE_PAYMENT"]["attempts"] >= 2
    assert stock(adm, product) == (2, 0)


# 4 ------------------------------------------------------------------------------------------
def test_4_two_customers_for_the_last_unit(adm):
    product = new_product(adm, stock=1)
    buyers = [new_customer(), new_customer()]
    barrier = threading.Barrier(2)

    def buy(user):
        barrier.wait()
        return checkout(user, product)

    with ThreadPoolExecutor(2) as pool:
        responses = list(pool.map(buy, buyers))

    codes = sorted(r.status_code for r in responses)
    assert codes == [201, 409]
    loser = next(r for r in responses if r.status_code == 409).json()["detail"]
    assert loser["code"] == "INSUFFICIENT_STOCK"
    assert stock(adm, product) == (0, 1)               # no overselling
    # The loser's release (tombstone) must not free the winner's unit
    wait_until(lambda: operations(adm, loser["order_id"])["RELEASE_INVENTORY"]["status"] == "DONE",
               timeout=30, what="release of the cancelled order")
    assert stock(adm, product) == (0, 1)


# 5 ------------------------------------------------------------------------------------------
def test_5_customer_cancels(adm):
    customer = new_customer()
    product = new_product(adm, stock=4)
    o = checkout(customer, product, quantity=3).json()
    assert stock(adm, product) == (1, 3)

    cancelled = cancel(customer, o["id"])

    assert cancelled.status_code == 200
    assert (cancelled.json()["status"], cancelled.json()["cancel_reason"]) == ("CANCELLED", "USER")
    assert stock(adm, product) == (4, 0)
    assert len(notifications(customer, "ORDER_CANCELLED", o["id"])) == 1
    assert cancel(customer, o["id"]).status_code == 200            # repeated: same result
    assert len(notifications(customer, "ORDER_CANCELLED", o["id"])) == 1


# 6 ------------------------------------------------------------------------------------------
def test_6_order_expires(adm):
    """compose.e2e.yaml sets ORDER_RESERVATION_TTL_MINUTES=1 (only for these tests)."""
    customer = new_customer()
    product = new_product(adm, stock=2)
    o = checkout(customer, product, quantity=2).json()
    assert stock(adm, product) == (0, 2)

    final = wait_until(lambda: (lambda x: x["status"] == "CANCELLED" and x)(order(customer, o["id"])),
                       timeout=150, interval=2, what="expiration")

    assert final["cancel_reason"] == "EXPIRED"
    wait_until(lambda: stock(adm, product) == (2, 0), timeout=30, what="stock released")
    wait_until(lambda: notifications(customer, "ORDER_CANCELLED", o["id"]), timeout=30, what="ORDER_CANCELLED")
    assert Decimal(final["total"]) == Decimal("51.00")
