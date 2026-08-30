def check_login_time(user_id):
    user = db.query(User).filter(id=user_id).first()
    return user.last_login

def update_login(req):
    last = check_login_time(req.user_id)
    db.execute("UPDATE User SET last_login = now()")