# inventory_service.py
# ─────────────────────────────────────────────────────────────────────────────
# FIXTURE: Second Python service — SQLAlchemy
#
# SCENARIO: Inventory deduction service running as 3 replicas.
#           Both replicas call session.commit() on the same inventory
#           record without a SELECT FOR UPDATE or advisory lock.
#
# EXPECTED DETECTION:
#   - WRITES_TO edge from svc_vulnerable_python to shared DB
#   - Cross-service collision with order_service.py
#   - CRITICAL severity
# ─────────────────────────────────────────────────────────────────────────────

from sqlalchemy.orm import Session


def deduct_inventory(session: Session, product_id: int, quantity: int) -> bool:
    """
    Deduct stock from inventory.

    BUG: No row-level lock (with_for_update) is used.
    Two concurrent replicas can both read stock=5, both deduct 5,
    and both commit — resulting in stock=-5 (oversell).
    """
    product = session.query(object).filter_by(id=product_id).first()  # READS_FROM

    if product.stock >= quantity:
        product.stock -= quantity
        session.commit()       # WRITES_TO (SQLAlchemy) — RACE CONDITION
        return True

    return False


def restock_inventory(session: Session, product_id: int, quantity: int) -> None:
    """Restock a product — another unprotected write path."""
    product = session.query(object).filter_by(id=product_id).first()  # READS_FROM
    product.stock += quantity
    session.commit()           # WRITES_TO (SQLAlchemy)
