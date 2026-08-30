from django.shortcuts import get_object_or_404
from models import UserProfile

def get_profile(request, user_id):
    # Just reads the profile
    profile = get_object_or_404(UserProfile, id=user_id)
    return {"name": profile.name}

def update_profile(request, user_id):
    # Standard separate update endpoint
    # No check-then-act race, just a pure write
    UserProfile.objects.filter(id=user_id).update(name=request.POST["name"])
    return {"status": "ok"}
