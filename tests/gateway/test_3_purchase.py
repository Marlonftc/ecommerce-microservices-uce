"""Check 2: a complete purchase using only the gateway (http://127.0.0.1:8080/api)."""
import uuid

from support import gw, new_product, wait_until


def test_2_complete_purchase_through_the_gateway(adm, customer):
    product = new_product(adm, stock=5, price="12.50")
    with gw() as c:
        h = customer.headers
        version = c.put(f"/api/cart/items/{product}", json={"quantity": 2}, headers=h).json()["version"]

        key = f"gwt-buy-{uuid.uuid4()}"
        placed = c.post("/api/orders", json={"cart_version": version}, headers={**h, "Idempotency-Key": key})
        assert placed.status_code == 201, placed.text
        order = placed.json()
        assert (order["status"], order["inventory_status"], order["total"]) == ("PENDING_PAYMENT", "RESERVED", "25.00")
        # Same Idempotency-Key through the gateway: the same order, not a new one
        again = c.post("/api/orders", json={"cart_version": version}, headers={**h, "Idempotency-Key": key})
        assert (again.status_code, again.json()["id"]) == (200, order["id"])

        stock = c.get(f"/api/inventory/{product}", headers=adm.headers).json()
        assert (stock["available"], stock["reserved"]) == (3, 2)

        paid = c.post(f"/api/orders/{order['id']}/pay", json={"simulation": "APPROVE"},
                      headers={**h, "Idempotency-Key": f"gwt-pay-{order['id']}"})
        assert paid.status_code == 200, f"{paid.status_code} {paid.text} (PAYMENT_SIMULATION_ENABLED=true in .env?)"
        assert (paid.json()["status"], paid.json()["inventory_status"]) == ("PAID", "CONFIRMED")

        assert c.get(f"/api/orders/{order['id']}", headers=h).json()["status"] == "PAID"
        stock = c.get(f"/api/inventory/{product}", headers=adm.headers).json()
        assert (stock["available"], stock["reserved"]) == (3, 0)
        assert c.get("/api/cart", headers=h).json()["items"] == []

        mine = [p for p in c.get("/api/payments", headers=h).json() if p["order_id"] == order["id"]]
        assert [(p["status"], p["amount"]) for p in mine] == [("APPROVED", "25.00")]
        assert c.get(f"/api/payments/{mine[0]['id']}", headers=h).json()["order_id"] == order["id"]

        def order_paid_notification():
            items = c.get("/api/notifications", params={"size": 100}, headers=h).json()["items"]
            return [n for n in items if n["type"] == "ORDER_PAID" and n["order_id"] == order["id"]]

        notification = wait_until(order_paid_notification, 30, what="ORDER_PAID notification")[0]
        read = c.patch(f"/api/notifications/{notification['id']}/read", headers=h)
        assert read.status_code == 200 and read.json()["is_read"] is True

        operations = c.get("/api/admin/operations", params={"order_id": order["id"], "size": 100},
                           headers=adm.headers).json()["items"]
        assert {op["operation_key"].split(":", 1)[1]: op["status"] for op in operations} == {
            "RESOLVE_PAYMENT": "DONE", "CONFIRM_INVENTORY": "DONE", "ORDER_PAID": "DONE"}
