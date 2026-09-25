import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db.models import Base

_DB_URL = os.getenv("DATABASE_URL", "").strip() or "sqlite:///trading.db"

# Render / Heroku may export postgres://. SQLAlchemy 2.1 defaults a bare
# postgresql:// URL to the psycopg (v3) dialect, while this application ships
# psycopg2-binary for broad Python/Render compatibility. Select that driver
# explicitly so the URL works on both the Docker and native Python services.
if _DB_URL.startswith("postgres://"):
    _DB_URL = _DB_URL.replace("postgres://", "postgresql://", 1)
if _DB_URL.startswith("postgresql://"):
    _DB_URL = _DB_URL.replace("postgresql://", "postgresql+psycopg2://", 1)
elif _DB_URL.startswith("postgresql+psycopg://"):
    _DB_URL = _DB_URL.replace("postgresql+psycopg://", "postgresql+psycopg2://", 1)

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


def init_db():
    """Create all tables if they don't exist, then add any new columns."""
    Base.metadata.create_all(bind=engine)
    _migrate_add_columns()
    _backfill_trade_strategy_tags()


def _backfill_trade_strategy_tags():
    """Tag legacy option trades (saved before per-strategy tagging) with each
    user's default options strategy, so the Results per-strategy filter shows
    them instead of nothing."""
    import logging as _lg
    log = _lg.getLogger(__name__)
    try:
        from sqlalchemy.orm import Session
        from db.models import Trade, Strategy
        with Session(engine) as db:
            untagged = (db.query(Trade)
                        .filter(Trade.strategy_id.is_(None))
                        .filter(Trade.position_type.in_(("CALL", "PUT")))
                        .all())
            if not untagged:
                return
            # cache each user's options strategy (oldest = the seeded default)
            opt_strat = {}
            tagged = 0
            for tr in untagged:
                if tr.user_id not in opt_strat:
                    s = (db.query(Strategy)
                         .filter(Strategy.user_id == tr.user_id)
                         .filter((Strategy.instrument_type == "OPTIONS") |
                                 (Strategy.instrument_type.is_(None)))
                         .order_by(Strategy.id).first())
                    opt_strat[tr.user_id] = s
                s = opt_strat[tr.user_id]
                if s:
                    tr.strategy_id = s.id
                    tr.strategy_name = s.name
                    tagged += 1
            if tagged:
                db.commit()
                log.info(f"Backfilled strategy tags on {tagged} legacy option trade(s).")
    except Exception as e:
        log.warning(f"trade strategy-tag backfill skipped: {e}")


def _migrate_add_columns():
    """Add new profile columns to existing tables without destroying data.

    PostgreSQL: uses ADD COLUMN IF NOT EXISTS (9.6+) — idempotent, no error.
    SQLite:     IF NOT EXISTS was added in 3.37 (2021); for older versions we
                catch the "duplicate column" error and continue.
    """
    import logging as _logging
    _log = _logging.getLogger(__name__)

    # Boolean defaults must be DB-specific:
    #   PostgreSQL → TRUE/FALSE   |   SQLite → 1/0
    bool_true  = "TRUE"  if not _is_sqlite else "1"
    bool_false = "FALSE" if not _is_sqlite else "0"

    _new_cols = [
        ("users", "auth_version", "INTEGER DEFAULT 0"),
        ("users",  "display_name",           "VARCHAR(150)"),
        ("users",  "bio",                    "TEXT"),
        ("users",  "photo_base64",           "TEXT"),
        ("users",  "trade_confirm_modal",    f"BOOLEAN DEFAULT {bool_true}"),
        ("users",  "broker_id",              "VARCHAR(100)"),
        ("users",  "settings_json",           "TEXT"),
        ("users",  "kite_api_key_stored",    "VARCHAR(100)"),
        ("users",  "kite_api_secret_enc",    "TEXT"),
        ("users",  "kite_access_token_enc",  "TEXT"),
        ("users",  "kite_token_date",        "DATE"),
        ("trades", "trade_mode",             "VARCHAR(10) DEFAULT 'PAPER'"),
        ("users",  "is_admin",               f"BOOLEAN DEFAULT {bool_false}"),
        ("users",  "background_trading",     f"BOOLEAN DEFAULT {bool_true}"),
        ("watchlist", "list_name",           "VARCHAR(100) DEFAULT 'My Watchlist'"),
        ("strategies", "instrument_type",    "VARCHAR(10) DEFAULT 'OPTIONS'"),
        ("strategies", "symbol",             "VARCHAR(50) DEFAULT 'NIFTY 50'"),
        ("strategies", "engine_type",        "VARCHAR(20) DEFAULT 'ORB'"),
        ("strategies", "is_running",         f"BOOLEAN DEFAULT {bool_false}"),
        ("trades",     "strategy_id",        "INTEGER"),
        ("trades",     "strategy_name",      "VARCHAR(200)"),
        ("strategies", "run_mode",           "VARCHAR(10)"),
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
                    conn.rollback()
                    raise RuntimeError(f'Migration failed for {table}.{col}') from e
