"""
Live / Paper Trading Entry Point
---------------------------------
Usage:
    python main.py

Set APP_MODE in .env (or Render env vars):
    BACKTEST  — replay today's 1-min data through the strategy
    PAPER     — live market feed, no real orders placed
    LIVE      — live market feed, real orders placed on Kite

Authentication:
    API key and access token come exclusively from the user's profile in the DB.
    Set them once on the /profile page — no env vars, no restart required.

    KITE_API_KEY / KITE_API_SECRET env vars are fully optional and ignored when
    a profile token is found in the DB.
"""

import logging
import threading
from typing import Optional

from config.settings import AppConfig, TradingConfig
from core.state import BotState
from dashboard import create_app
from db.database import init_db, SessionLocal
from db.models import User
from execution.broker import KiteBroker
from execution.historical_backtest import HistoricalBacktester
from execution.trading_engine import TradingEngine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# Shared mutable references so the engine can be started after token is saved via profile
_current_engine: TradingEngine = None
_engine_thread: threading.Thread = None


def _find_restore_user_id() -> Optional[int]:
    """
    Return the user_id whose DB profile has a valid today Kite token.
    Preference order:
      1. User matching DEFAULT_USER_EMAIL env var
      2. First user in DB that has a valid today token
    Returns None if no eligible user is found.
    """
    import datetime as _dt
    import os
    from execution.broker import _IST

    today = _dt.datetime.now(tz=_IST).date()
    db = SessionLocal()
    try:
        default_email = os.getenv("DEFAULT_USER_EMAIL", "").strip().lower()
        if default_email:
            u = db.query(User).filter_by(email=default_email).first()
            if u and u.kite_access_token_enc and u.kite_token_date == today:
                return u.id

        # Fall back: any user with a valid today token
        u = (db.query(User)
               .filter(User.kite_access_token_enc.isnot(None),
                       User.kite_token_date == today)
               .first())
        return u.id if u else None
    except Exception as e:
        logger.warning(f"_find_restore_user_id failed: {e}")
        return None
    finally:
        db.close()


def main():
    global _current_engine, _engine_thread

    app_config     = AppConfig()
    trading_config = TradingConfig()

    # KITE_API_KEY / KITE_API_SECRET are now optional.
    # The broker will be re-initialised with the profile-stored api_key when
    # restore_from_db() succeeds.
    if not trading_config.api_key:
        logger.info("KITE_API_KEY not set — will rely on profile-stored api_key from DB.")

    # Initialise DB early so we can try profile-based restore.
    init_db()

    state  = BotState(app_mode=app_config.mode)
    broker = KiteBroker(trading_config)

    # ── 1. DB profile restore — always tried first (profile api_key is source of truth) ──
    uid = _find_restore_user_id()
    if uid is not None:
        db = SessionLocal()
        try:
            authenticated = broker.restore_from_db(db, uid)
            if authenticated:
                logger.info(f"Session restored from DB profile (user_id={uid}).")
        finally:
            db.close()
    else:
        authenticated = False

    # ── 2. Fall back to env-var / file-cache (local dev without DB token) ──────
    if not authenticated:
        authenticated = broker.restore_session()

    # ── 3. Not authenticated — show banner, prompt user via profile page ──────
    if not authenticated:
        logger.warning(
            "No Kite session found. "
            "Open /profile in your browser, enter your API Key + Access Token, and hit Save."
        )
        state.kite_auth_error = True
        state.logs.append("[--:--:--] ⚠ Not authenticated — open /profile to add your Kite token")
    else:
        logger.info("Session restored — skipping login.")

    def _start_engine(mode: str):
        global _current_engine, _engine_thread
        engine = TradingEngine(trading_config, state, broker, user_id=uid)
        _current_engine = engine

        def _run():
            logger.info(f"Engine starting in {mode} mode.")
            state.logs.append(f"[--:--:--] ▶ Switched to {mode} mode")
            if mode == "BACKTEST":
                engine.run_backtest()
            elif mode == "PAPER":
                engine.run_live(real_money=False)
            elif mode == "LIVE":
                engine.run_live(real_money=True)

        t = threading.Thread(target=_run, daemon=True, name=f"Engine-{mode}")
        _engine_thread = t
        t.start()

    def switch_mode(new_mode: str):
        global _current_engine
        logger.info(f"Mode switch requested: {state.app_mode} → {new_mode}")
        if _current_engine:
            _current_engine.stop()
        state.reset(new_mode)
        _start_engine(new_mode)

    # Only start the engine if already authenticated
    if authenticated:
        _start_engine(app_config.mode)

    backtester = HistoricalBacktester(trading_config, broker)
    flask_app = create_app(
        state,
        mode_switcher=switch_mode,
        backtester=backtester,
        trading_config=trading_config,
        broker=broker,
        start_engine_fn=_start_engine,
        initial_mode=app_config.mode,
        user_id=uid,
    )
    logger.info(f"Dashboard live at http://{app_config.host}:{app_config.port}")
    flask_app.run(host=app_config.host, port=app_config.port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
