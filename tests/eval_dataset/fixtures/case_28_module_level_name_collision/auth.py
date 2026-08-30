def validate(token):
    # Reads from tokens
    db.execute("SELECT * FROM tokens WHERE id = " + token)
    return True
