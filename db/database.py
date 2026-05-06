import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db.models import Base

_DB_URL = os.getenv("DATABASE_URL", "sqlite:///trading.db")
_connect_args = {"check_same_thread": False} if _DB_URL.startswith("sqlite") else {}

engine = create_engine(_DB_URL, connect_args=_connect_args, echo=False)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    """Yield a DB session and always close it after use."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Create all tables if they don't exist."""
    Base.metadata.create_all(bind=engine)
