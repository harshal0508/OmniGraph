import db

def deposit(acc_id, amount):
    # READ + WRITE in function 1
    balance = db.query("SELECT balance FROM accounts WHERE id=1")
    db.execute("UPDATE accounts SET balance = balance + 10")

def withdraw(acc_id, amount):
    # READ + WRITE in function 2
    balance = db.query("SELECT balance FROM accounts WHERE id=1")
    if balance > 10:
        db.execute("UPDATE accounts SET balance = balance - 10")
