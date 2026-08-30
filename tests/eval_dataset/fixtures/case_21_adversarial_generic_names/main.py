def func_a(cart_id):
    cart = db.query(Cart).filter(id=cart_id).first()
    return cart.items_count

def handle_request(req):
    count = func_a(req.cart_id)
    if count < 5:
        db.execute("UPDATE Cart SET items_count = items_count + 1")