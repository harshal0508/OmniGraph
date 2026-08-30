def check_fraud():
    db.execute('UPDATE orders SET fraud = 1')
