"""Scenarios 7-12: failures and recovery with the real containers.

Each test leaves every container running again (finally blocks). They never
remove volumes: the data of the project stays as it was.
"""
import pytest

from support import (checkout, compose, containers, new_customer, new_product, notifications, operations,
                     order, pay, stock, wait_healthy, wait_until)

pytestmark = pytest.mark.docker


def paid_order_with_pending_notification(adm, notification_down=True):
    customer = new_customer()
    product = new_product(adm, stock=3)
    o = checkout(customer, product).json()
    paid = pay(customer, o["id"])
    assert paid.json()["status"] == "PAID"
    return customer, product, o


# 7 ------------------------------------------------------------------------------------------
def test_7_inventory_down_then_sweep(adm):
    customer = new_customer()
    product = new_product(adm, stock=4)
    compose("stop", "inventory-service")
    try:
        placed = checkout(customer, product, quantity=2)
        assert placed.status_code == 202
        o = placed.json()
        assert (o["status"], o["inventory_status"]) == ("RESERVING", "PENDING")
    finally:
        compose("start", "inventory-service")
        wait_healthy("inventory-service")

    final = wait_until(lambda: (lambda x: x["status"] == "CANCELLED" and x)(order(customer, o["id"])),
                       timeout=180, interval=2, what="RESERVING sweep")
    assert final["cancel_reason"] == "RESERVATION_TIMEOUT"
    wait_until(lambda: operations(adm, o["id"])["RELEASE_INVENTORY"]["status"] == "DONE",
               timeout=60, what="release done")
    assert stock(adm, product) == (4, 0)                 # nothing kept
    assert notifications(customer, "ORDER_CANCELLED", o["id"]) == []   # never confirmed: no message


# 8 ------------------------------------------------------------------------------------------
def test_8_notification_down_then_delivered(adm):
    compose("stop", "notification-service")
    try:
        customer, product, o = paid_order_with_pending_notification(adm)
        assert order(customer, o["id"])["inventory_status"] == "CONFIRMED"   # the purchase is kept
        op = operations(adm, o["id"])["ORDER_PAID"]
        assert op["status"] == "PENDING" and op["attempts"] >= 1
    finally:
        compose("start", "notification-service")
        wait_healthy("notification-service")

    wait_until(lambda: operations(adm, o["id"])["ORDER_PAID"]["status"] == "DONE", timeout=90,
               what="notification delivered by the worker")
    assert len(notifications(customer, "ORDER_PAID", o["id"])) == 1
    assert stock(adm, product) == (2, 0)


# 9 ------------------------------------------------------------------------------------------
def test_9_worker_killed_while_holding_a_lease(adm):
    compose("pause", "notification-service")          # calls hang until the read timeout
    killed = False
    try:
        customer, product, o = paid_order_with_pending_notification(adm)
        # Synchronization point: the WORKER (not the request) holds the lease of this operation
        claimed = wait_until(
            lambda: (lambda op: op["locked_until"] and op["attempts"] >= 2 and op)(
                operations(adm, o["id"])["ORDER_PAID"]),
            timeout=60, interval=0.3, what="worker lease on ORDER_PAID")
        compose("kill", "order-worker")
        killed = True
        after_kill = operations(adm, o["id"])["ORDER_PAID"]
        assert after_kill["status"] != "DONE" and after_kill["locked_until"] == claimed["locked_until"]
    finally:
        compose("unpause", "notification-service")
        if killed:
            compose("start", "order-worker")
        wait_healthy("order-worker")

    done = wait_until(lambda: (lambda op: op["status"] == "DONE" and op)(operations(adm, o["id"])["ORDER_PAID"]),
                      timeout=90, what="recovery after the lease expired")
    assert done["attempts"] >= claimed["attempts"]
    # The killed call may have reached notification-service before dying; the retry
    # repeats it, and the event_id makes sure the customer still gets ONE message
    assert len(notifications(customer, "ORDER_PAID", o["id"])) == 1
    assert order(customer, o["id"])["status"] == "PAID"
    assert stock(adm, product) == (2, 0)


# 10 -----------------------------------------------------------------------------------------
def test_10_restart_and_recreate_keep_everything(adm):
    compose("stop", "notification-service")
    try:
        customer, product, o = paid_order_with_pending_notification(adm)
        before = {s: [c["ID"] for c in containers(s)] for s in ("order-service", "order-worker")}

        compose("restart", "order-service", "order-worker")
        wait_healthy("order-service")
        wait_healthy("order-worker")
        assert order(customer, o["id"])["status"] == "PAID"

        compose("up", "-d", "--force-recreate", "--no-deps", "order-service", "order-worker")
        wait_healthy("order-service")
        wait_healthy("order-worker")
        after = {s: [c["ID"] for c in containers(s)] for s in ("order-service", "order-worker")}
        assert all(set(before[s]).isdisjoint(after[s]) for s in before)   # new containers
        assert order(customer, o["id"])["status"] == "PAID"
        assert operations(adm, o["id"])["ORDER_PAID"]["status"] == "PENDING"  # still saved
    finally:
        compose("start", "notification-service")
        wait_healthy("notification-service")

    wait_until(lambda: operations(adm, o["id"])["ORDER_PAID"]["status"] == "DONE", timeout=90,
               what="work saved before the restart finished by the new worker")
    assert len(notifications(customer, "ORDER_PAID", o["id"])) == 1


# 11 -----------------------------------------------------------------------------------------
def test_11_two_workers_without_duplicated_effects(adm):
    compose("up", "-d", "--no-deps", "--scale", "order-worker=2", "order-worker")
    try:
        wait_healthy("order-worker", count=2)
        compose("stop", "notification-service")
        try:
            purchases = [paid_order_with_pending_notification(adm) for _ in range(4)]
        finally:
            compose("start", "notification-service")
            wait_healthy("notification-service")

        for customer, product, o in purchases:
            wait_until(lambda: operations(adm, o["id"])["ORDER_PAID"]["status"] == "DONE", timeout=120,
                       what="notification by one of the two workers")
        for customer, product, o in purchases:
            assert len(notifications(customer, "ORDER_PAID", o["id"])) == 1   # one message each
            assert stock(adm, product) == (2, 0)                              # confirmed once
            assert order(customer, o["id"])["inventory_status"] == "CONFIRMED"
    finally:
        compose("up", "-d", "--no-deps", "--scale", "order-worker=1", "order-worker")
        wait_healthy("order-worker", count=1)


# 12 -----------------------------------------------------------------------------------------
def test_12_graceful_stop(adm):
    compose("stop", "order-worker")
    try:
        stopped = containers("order-worker")
        assert [(c["State"], c.get("ExitCode")) for c in stopped] == [("exited", 0)]
        logs = compose("logs", "--no-log-prefix", "--tail", "50", "order-worker").stdout
        assert "stop requested" in logs and "worker stopped" in logs
    finally:
        compose("start", "order-worker")
        wait_healthy("order-worker")
