from sqlalchemy import create_engine, Column, Integer, String
from sqlalchemy.orm import declarative_base

Base = declarative_base()

class Orders(Base):
    __tablename__ = 'orders'
    id = Column(Integer, primary_key=True)
    status = Column(String)

def handle_order():
    # Write to orders
    new_order = Orders(status="PENDING")
    db.add(new_order)
    db.commit()
