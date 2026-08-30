from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from .models import OIMEFight

def give_up(request, pk):
    # Pre-fix TOCTOU code
    attempt = get_object_or_404(OIMEFight, contributor=contributor, proposal=proposal)
    
    if attempt.status == "OIME_TBD" and attempt.time_expired:
        attempt.status = "OIME_TLE"
        attempt.submitted_at = timezone.now()
        attempt.save()
        return redirect("oime-proposal-detail", pk)

    if attempt.status == "OIME_TBD":
        recent_give_ups = OIMEFight.objects.filter(status="OIME_FAIL").count()
        if recent_give_ups >= 3:
            return redirect("oime-proposal-fight", pk)
            
        attempt.status = "OIME_FAIL"
        attempt.submitted_at = timezone.now()
        attempt.save()

    return redirect("oime-proposal-detail", pk)
