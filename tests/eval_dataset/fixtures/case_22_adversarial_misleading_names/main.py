def validate_email(req):
    profile = db.query(UserProfile).filter(email=req.email).first()
    return profile is not None

def claim(req):
    db.execute("UPDATE UserProfile SET claimed = true")