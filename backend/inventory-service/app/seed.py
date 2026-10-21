"""Loads the initial stock of the initial catalog. Safe to run many times.

The product ids must match the initial catalog of product-service. The sku
is kept here only as documentation and for the cross-check script.
"""
import logging

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.models import Stock, StockAdjustment

logging.basicConfig(level=settings.log_level)
logger = logging.getLogger("seed")

INITIAL_STOCK = [
    {"product_id": 1, "sku": "LAP-001", "available": 15},
    {"product_id": 2, "sku": "LAP-002", "available": 8},
    {"product_id": 3, "sku": "MON-001", "available": 20},
    {"product_id": 4, "sku": "TEC-001", "available": 30},
    {"product_id": 5, "sku": "MOU-001", "available": 50},
    {"product_id": 6, "sku": "AUD-001", "available": 25},
    {"product_id": 7, "sku": "SSD-001", "available": 40},
    {"product_id": 8, "sku": "USB-001", "available": 100},
]


def seed_stock(db: Session) -> int:
    """Creates the stock rows that do not exist yet. Returns how many were created."""
    rows = [{"product_id": s["product_id"], "available": s["available"]} for s in INITIAL_STOCK]
    created = db.execute(
        insert(Stock)
        .values(rows)
        .on_conflict_do_nothing()  # never overwrite later movements
        .returning(Stock.product_id, Stock.available)
    ).all()

    # One history entry per initial load, with a fixed key so it is also idempotent
    if created:
        db.execute(
            insert(StockAdjustment)
            .values([
                {
                    "idempotency_key": f"seed-{product_id}",
                    "product_id": product_id,
                    "delta": available,
                    "available_before": 0,
                    "available_after": available,
                    "admin_user_id": None,
                    "reason": "initial stock",
                }
                for product_id, available in created
            ])
            .on_conflict_do_nothing()
        )
    db.commit()
    return len(created)


if __name__ == "__main__":
    with SessionLocal() as session:
        count = seed_stock(session)
    logger.info("initial stock: %d products created, %d already existed",
                count, len(INITIAL_STOCK) - count)
