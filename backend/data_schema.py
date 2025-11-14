from sqlalchemy import create_engine, Column, Integer, String, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime
from pydantic import BaseModel
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_FILE_PATH = PROJECT_ROOT / "hazard_log.db"

SQLALCHEMY_DATABASE_URL = f"sqlite:///{DB_FILE_PATH.as_posix()}"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

class Hazard(Base):
    __tablename__ = "hazards"

    id = Column(Integer, primary_key=True, index=True)
    hazard_type = Column(String, index=True)
    timestamp = Column(DateTime, default=datetime.utcnow)
    location_data = Column(String)
    severity = Column(Integer)

class HazardCreate(BaseModel):
    hazard_type: str
    location_data: str
    severity: int

    class Config:
        orm_mode = True

def create_db_tables():
    Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

create_db_tables()
