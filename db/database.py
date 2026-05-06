import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db.models import Base

_DB_URL = os.getenv("DATABASE_URL", "sqlite:///trading.db")

# Render / Heroku export postgres:// but SQLAlchemy needs postgresql://
if _DB_URL.startswith("postgres://"):
    _DB_URL = _DB_URL.replace("postgres://", "postgresql://", 1)

_is_sqlite = _DB_URL.startswith("sqlite")

if _is_sqlite:
    engine = create_engine(
        _DB_URL,
        connect_args={"check_same_thread": False},
        echo=False,
    )
else:
    # PostgreSQL — pool settings suited for a long-running web app
    engine = create_engine(
        _DB_URL,
        pool_pre_ping=True,      # detect stale connections
        pool_recycle=300,        # recycle connections every 5 min (avoids cloud timeouts)
        echo=False,
    )

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
