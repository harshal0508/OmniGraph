from sqlalchemy import create_engine, Column, Integer, String
from sqlalchemy.orm import declarative_base

Base = declarative_base()

class Logs(Base):
    __tablename__ = 'logs'
    id = Column(Integer, primary_key=True)
    message = Column(String)

def write_log(msg):
    # Notice: No read! Just an insert. This is safe even with replicas=3.
    new_log = Logs(message=msg)
    db.add(new_log)
    db.commit()
