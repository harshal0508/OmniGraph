from models import Cart

def check_cart_status(cart_id):
    cart = Cart.objects.get(id=cart_id)
    return cart.status == 'open'
