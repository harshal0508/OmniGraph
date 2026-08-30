# order_service.py
# ─────────────────────────────────────────────────────────────────────────────
# FIXTURE: Vulnerable Python service — Django ORM
#
# SCENARIO: Two replicas of this service concurrently handle
#           payment confirmation. Both read the order, check status,
#           and call order.save() with NO lock or transaction wrapper.
#
# EXPECTED DETECTION:
#   - WRITES_TO edge from svc_vulnerable_python to the shared DB
#   - Collision: TOCTOU pattern (read-check-write without lock)
#   - Severity: CRITICAL (replica_count > 1 from K8s fixture)
# ─────────────────────────────────────────────────────────────────────────────

from django.db import models


class Order(models.Model):
    status = models.CharField(max_length=50, default="pending")
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    user_id = models.IntegerField()


def confirm_payment(order_id: int, payment_amount: float) -> dict:
    """
    Process payment for an order.

    BUG: Two replicas calling this simultaneously will both pass the
    status check and both execute order.save(), causing a double-charge.
    There is no SELECT FOR UPDATE and no transaction wrapper here.
    """
    # EC-7: TOCTOU — read, then conditional write with no lock
    order = Order.objects.get(id=order_id)   # READS_FROM

    if order.status == "pending":
        order.status = "paid"
        order.amount = payment_amount
        order.save()                          # WRITES_TO (Django ORM)
        return {"status": "success", "order_id": order_id}

    return {"status": "already_processed"}


def update_order_metadata(order_id: int, metadata: dict) -> None:
    """Update order metadata — also an unprotected write."""
    order = Order.objects.get(id=order_id)   # READS_FROM
    order.status = metadata.get("status", order.status)
    order.save()                              # WRITES_TO (Django ORM) — second writer path
