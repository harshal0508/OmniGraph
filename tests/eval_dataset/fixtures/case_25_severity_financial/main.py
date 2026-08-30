def check_wallet_balance(wallet_id):
    wallet = db.query(Wallet).filter(id=wallet_id).first()
    return wallet.balance

def process_withdrawal(req):
    bal = check_wallet_balance(req.wallet_id)
    if bal >= req.amount:
        db.execute("UPDATE Wallet SET balance = balance - %s", req.amount)