from sqlalchemy.orm import Session
from models import EventPromoCode

def _validate_promo_code(db: Session, code_str: str, event_id: int):
    return db.query(EventPromoCode).filter_by(code=code_str, event_id=event_id).first()

async def register_member(db: Session, request):
    # Pre-fix TOCTOU code: read promo
    promo = _validate_promo_code(db, request.promo_code, 1)

    if promo:
        # Simulate check-then-act gap
        if promo.times_used >= promo.max_uses:
            raise Exception("Promo exhausted")

        # Mutate in memory
        promo.times_used += 1

    # Pre-fix TOCTOU code: write promo
    db.commit()
    return True
