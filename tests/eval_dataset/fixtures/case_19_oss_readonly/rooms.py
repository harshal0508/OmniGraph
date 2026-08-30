from sqlalchemy.orm import Session
from models import Room

def list_rooms(db: Session):
    return db.query(Room).filter_by(roomstatus='available').all()

def get_room_details(db: Session, room_id: str):
    return db.query(Room).filter_by(id=room_id).first()
