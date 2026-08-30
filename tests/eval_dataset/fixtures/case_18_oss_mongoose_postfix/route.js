import { Cart } from '../models/Cart';
import { NextResponse } from 'next/server';

export async function PUT(request) {
  const { productId, quantity, userSession } = await request.json();

  // POST-FIX: Atomic update with findOneAndUpdate
  const cart = await Cart.findOneAndUpdate(
    { user: userSession.user.id, "items.product": productId },
    { $set: { "items.$.quantity": quantity } },
    { new: true }
  );

  return NextResponse.json({ cart });
}
