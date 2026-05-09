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
    """Create all tables if they don't exist, then add any new columns."""
    Base.metadata.create_all(bind=engine)
    _migrate_add_columns()


def _migrate_add_columns():
    """Add new columns to existing tables without destroying data (SQLite safe)."""
    _new_cols = [
        ("users", "display_name",        "VARCHAR(150)"),
        ("users", "bio",                 "TEXT"),
        ("users", "photo_base64",        "TEXT"),
        ("users", "trade_confirm_modal", "BOOLEAN DEFAULT 1"),
        ("users", "broker_id",           "VARCHAR(100)"),
    ]
    with engine.connect() as conn:
        for table, col, col_type in _new_cols:
            try:
                conn.execute(
                    __import__("sqlalchemy").text(
                        f"ALTER TABLE {table} ADD COLUMN {col} {col_type}"
                    )
                )
                conn.commit()
            except Exception:
                pass  # column already exists — ignore
