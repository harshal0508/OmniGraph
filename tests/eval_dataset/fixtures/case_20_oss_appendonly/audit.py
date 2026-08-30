from sqlalchemy.orm import Session
from models import AuditLog

def create_log(db: Session, action: str):
    # Pure write-only, no preceding read to race on
    log = AuditLog(action=action)
    db.add(log)
    db.commit()
    return log

def create_alert(db: Session, msg: str):
    log = AuditLog(action=f"ALERT: {msg}")
    db.add(log)
    db.commit()
    return log
