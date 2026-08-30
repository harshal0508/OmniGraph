def verificar_saldo(cuenta_id):
    wallet = db.query(Wallet).filter(id=cuenta_id).first()
    return wallet.balance

def retirar_fondos(req1):
    saldo = verificar_saldo(req1.cuenta_id)
    if saldo >= req1.monto:
        db.execute("UPDATE Wallet SET balance = balance - %s", req1.monto)