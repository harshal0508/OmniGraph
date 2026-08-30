from payments import validate

def process_checkout(order):
    if validate(order):
        # Writes to tokens
        db.execute("UPDATE tokens SET status='used' WHERE order_id=" + order.id)
