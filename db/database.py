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
    """Add new profile columns to existing tables without destroying data.

    PostgreSQL: uses ADD COLUMN IF NOT EXISTS (9.6+) — idempotent, no error.
    SQLite:     IF NOT EXISTS was added in 3.37 (2021); for older versions we
                catch the "duplicate column" error and continue.
    """
    import logging as _logging
    _log = _logging.getLogger(__name__)

    # Boolean default must be DB-specific:
    #   PostgreSQL → TRUE/FALSE   |   SQLite → 1/0
    bool_true = "TRUE" if not _is_sqlite else "1"

    _new_cols = [
        ("users", "display_name",        "VARCHAR(150)"),
        ("users", "bio",                 "TEXT"),
        ("users", "photo_base64",        "TEXT"),
        ("users", "trade_confirm_modal", f"BOOLEAN DEFAULT {bool_true}"),
        ("users", "broker_id",           "VARCHAR(100)"),
    ]

    from sqlalchemy import text as _text

    with engine.connect() as conn:
        for table, col, col_type in _new_cols:
            if _is_sqlite:
                # SQLite: no IF NOT EXISTS before 3.37 — just catch the error
                try:
                    conn.execute(_text(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}"))
                    conn.commit()
                    _log.info(f"Migration: added column {table}.{col}")
                except Exception as e:
                    if "duplicate column" in str(e).lower() or "already exists" in str(e).lower():
                        pass  # column already there — ok
                    else:
                        _log.warning(f"Migration: unexpected error adding {table}.{col}: {e}")
            else:
                # PostgreSQL: IF NOT EXISTS makes it fully idempotent
                try:
                    conn.execute(_text(
                        f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {col_type}"
                    ))
                    conn.commit()
                    _log.info(f"Migration: ensured column {table}.{col}")
                except Exception as e:
                    _log.warning(f"Migration: error adding {table}.{col}: {e}")
