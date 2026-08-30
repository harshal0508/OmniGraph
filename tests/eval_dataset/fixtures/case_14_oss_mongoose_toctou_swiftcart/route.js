import { Cart } from '../models/Cart';
import { Product } from '../models/Product';
import { NextResponse } from 'next/server';

export async function PUT(request) {
  const { productId, quantity, userSession } = await request.json();

  // Pre-fix TOCTOU code: read cart
  const cart = await Cart.findOne({ user: userSession.user.id });
  if (!cart) {
    return NextResponse.json({ error: "Cart not found" }, { status: 404 });
  }

  const itemIndex = cart.items.findIndex(
    (item) => item.product.toString() === productId
  );

  const product = await Product.findById(productId);
  if (!product) {
    return NextResponse.json({ error: "Product not found" }, { status: 404 });
  }

  if (quantity > product.quantity) {
    return NextResponse.json({ error: "Not enough stock" }, { status: 400 });
  }

  // Pre-fix TOCTOU code: mutate in-memory and save
  cart.items[itemIndex].quantity = quantity;
  cart.items[itemIndex].price = product.price;

  await cart.save();

  return NextResponse.json({ message: "Cart updated", cart });
}
