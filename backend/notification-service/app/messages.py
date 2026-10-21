"""Texts shown to the customer, built from the event data.

They belong to this service: improving a text here does not change the
event contract with order-service, and texts are never used to compare
repeated events.
"""
from app.schemas import NotificationEventIn


def build_message(event: NotificationEventIn) -> tuple[str, str]:
    """Returns (title, message) in Spanish."""
    order = str(event.order_id)[:8]  # short reference, enough for the customer
    if event.type == "ORDER_PAID":
        return ("Pago confirmado",
                f"Tu pedido {order} fue pagado por {event.amount} {event.currency}. "
                "Gracias por tu compra.")
    if event.type == "PAYMENT_REJECTED":
        return ("Pago rechazado",
                f"El pago de {event.amount} {event.currency} del pedido {order} fue rechazado. "
                "Puedes realizar un nuevo pedido.")
    return ("Pedido cancelado",
            f"Tu pedido {order} fue cancelado y los productos reservados se liberaron.")
