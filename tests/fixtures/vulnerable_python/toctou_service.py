# toctou_service.py
# ---------------------------------------------------------------------------
# FIXTURE: Python service demonstrating a classic TOCTOU pattern.
#
# TOCTOU = Time-of-Check-Time-of-Use
#
# SCENARIO:
#   A coupon redemption service. Two replicas receive requests at the same
#   millisecond from the same user. Both check that the coupon is valid and
#   not yet used. Both pass the check. Both mark it as used and apply the
#   discount. User gets two discounts for one coupon.
#
# EXPECTED DETECTION:
#   - READS_FROM edge  (Order.objects.get / coupon.objects.get)
#   - WRITES_TO edge   (coupon.save() / order.save())
#   - Same table for both -> TOCTOU Race Condition finding
#   - Severity: CRITICAL (multi-replica K8s deployment)
# ---------------------------------------------------------------------------

from django.db import models
from django.utils import timezone


class Coupon(models.Model):
    code       = models.CharField(max_length=20, unique=True)
    is_used    = models.BooleanField(default=False)
    used_at    = models.DateTimeField(null=True)
    discount   = models.DecimalField(max_digits=5, decimal_places=2)
    owner_id   = models.IntegerField()


class Order(models.Model):
    user_id    = models.IntegerField()
    total      = models.DecimalField(max_digits=10, decimal_places=2)
    status     = models.CharField(max_length=50, default="pending")


def redeem_coupon(coupon_code: str, order_id: int) -> dict:
    """
    Apply a coupon to an order.

    BUG (TOCTOU): No SELECT FOR UPDATE.
    Two concurrent replicas both call coupon.objects.get() (READ),
    both see is_used=False (CHECK), both set is_used=True and save (WRITE).
    The coupon is used twice.

    Fix: use Coupon.objects.select_for_update().get(code=coupon_code)
    """
    coupon = Coupon.objects.get(code=coupon_code)   # READS_FROM  <-- window opens here
    order  = Order.objects.get(id=order_id)          # READS_FROM

    if not coupon.is_used:                           # CHECK  <-- race window
        coupon.is_used = True
        coupon.used_at = timezone.now()
        coupon.save()                                # WRITES_TO  <-- window closes (race!)

        order.total -= coupon.discount
        order.save()                                 # WRITES_TO  (second write)
        return {"status": "applied", "discount": str(coupon.discount)}

    return {"status": "already_used"}


def reserve_seat(event_id: int, user_id: int) -> dict:
    """
    Reserve one seat for an event.

    BUG (TOCTOU): read seats_available, check > 0, decrement and save.
    Two replicas both see seats_available=1, both decrement, both save.
    Seats go to -1 (overbooking).
    """
    from django.db.models import F

    # Simulated model query — READS_FROM
    event = type("Event", (), {
        "seats_available": 1,
        "save": lambda self: None,
    })()

    if event.seats_available > 0:                   # CHECK without lock
        event.seats_available -= 1
        event.save()                                 # WRITES_TO — overbooking race
        return {"status": "reserved"}

    return {"status": "sold_out"}
