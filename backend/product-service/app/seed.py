"""Loads the initial catalog. Safe to run many times.

The initial products use known ids (1 to 8) so that inventory-service can
load their stock. Products created later by the admin get new ids.
"""
import logging
from decimal import Decimal

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.models import Product

logging.basicConfig(level=settings.log_level)
logger = logging.getLogger("seed")

INITIAL_PRODUCTS = [
    {"id": 1, "sku": "LAP-001", "name": "Laptop 14 pulgadas Core i5 16 GB", "category": "laptops", "price": Decimal("749.99"),
     "description": "Laptop ligera para estudio y oficina, SSD de 512 GB."},
    {"id": 2, "sku": "LAP-002", "name": "Laptop 15.6 pulgadas Ryzen 7 16 GB", "category": "laptops", "price": Decimal("899.00"),
     "description": "Laptop de alto rendimiento para programación y diseño."},
    {"id": 3, "sku": "MON-001", "name": "Monitor 24 pulgadas Full HD", "category": "monitores", "price": Decimal("159.90"),
     "description": "Panel IPS, 75 Hz, entrada HDMI."},
    {"id": 4, "sku": "TEC-001", "name": "Teclado mecánico", "category": "accesorios", "price": Decimal("59.99"),
     "description": "Distribución en español, retroiluminado."},
    {"id": 5, "sku": "MOU-001", "name": "Mouse inalámbrico", "category": "accesorios", "price": Decimal("19.50"),
     "description": "Receptor USB, batería AA incluida."},
    {"id": 6, "sku": "AUD-001", "name": "Audífonos inalámbricos", "category": "audio", "price": Decimal("89.00"),
     "description": "Bluetooth, cancelación de ruido."},
    {"id": 7, "sku": "SSD-001", "name": "Disco SSD 1 TB NVMe", "category": "almacenamiento", "price": Decimal("74.99"),
     "description": "Lectura de hasta 3500 MB/s."},
    {"id": 8, "sku": "USB-001", "name": "Memoria USB 64 GB", "category": "almacenamiento", "price": Decimal("9.99"),
     "description": "USB 3.2."},
]


def seed_products(db: Session) -> int:
    """Inserts the initial products that do not exist yet. Returns how many were inserted."""
    statement = (
        insert(Product)
        .values(INITIAL_PRODUCTS)
        .on_conflict_do_nothing()  # never overwrite changes made by the admin
        .returning(Product.id)
    )
    inserted = len(db.execute(statement).all())

    # The ids were given explicitly, so the sequence must be moved past them.
    # Otherwise the first product created by the admin would try to use id 1.
    max_id = db.scalar(select(func.max(Product.id))) or 1
    db.execute(
        text("SELECT setval(pg_get_serial_sequence('products', 'id'), :max_id)"),
        {"max_id": max_id},
    )
    db.commit()
    return inserted


if __name__ == "__main__":
    with SessionLocal() as session:
        count = seed_products(session)
    logger.info("initial catalog: %d products inserted, %d already existed",
                count, len(INITIAL_PRODUCTS) - count)
