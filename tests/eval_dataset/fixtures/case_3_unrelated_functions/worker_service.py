import db

def nightly_report():
    # Only reads
    data = db.query("SELECT * FROM orders")
    return len(data)

def checkout():
    # Only writes
    db.execute("INSERT INTO orders (item) VALUES ('apple')")
