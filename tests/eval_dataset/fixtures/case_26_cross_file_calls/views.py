from models import Cart
from helpers import check_cart_status

def checkout_view(request):
    cart_id = request.POST.get('cart_id')
    # Read happens in helper
    if not check_cart_status(cart_id):
        return "Cart not ready"
    
    # Write happens in handler
    cart = Cart.objects.get(id=cart_id)
    cart.status = 'checked_out'
    cart.save()
    return "Success"
