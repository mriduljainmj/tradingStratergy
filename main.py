"""
Live / Paper Trading Entry Point
---------------------------------
Usage:
    python main.py

Multi-user: every registered user who has a valid Kite access token for today
gets their own isolated BotState + TradingEngine restored automatically on
startup.  New users can join at any time by:
  1. Registering an account via /login
  2. Authenticating with Kite via /profile  → engine starts automatically

Set APP_MODE in .env (or env vars) to choose the initial mode for restored
users:
    PAPER     — live market feed, no real orders (default)
    LIVE      — live market feed, real orders placed on Kite
    BACKTEST  — replay today's 1-min data through the strategy

KITE_API_KEY / KITE_API_SECRET are app-level credentials shared by all users
who haven't stored their own.  Set them once in .env — no per-user subscription
needed.
"""

import logging
import os
import threading
import time
import urllib.request

from config.settings import AppConfig
from core.engine_pool import engine_pool
from dashboard import create_app
from db.database import init_db, SessionLocal
from db.models import User
from execution.broker import _IST, decrypt_token

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def _restore_all_active_users(initial_mode: str):
    """
    Find all users with a valid today Kite token and restore their engines.
    Called once on startup so users who were active yesterday are ready
    immediately after the server restarts (within the same day).
    """
    import datetime
    today = datetime.datetime.now(tz=_IST).date()

    db = SessionLocal()
    try:
        active_users = (
            db.query(User)
            .filter(
                User.kite_access_token_enc.isnot(None),
                User.kite_token_date == today,
            )
            .all()
        )

        if not active_users:
            logger.info(
                "No users with a valid today Kite token found. "
                "Open /profile in your browser and authenticate with Kite."
            )
            return

        for user in active_users:
            # Resolve the API key (user's own > app env var)
            api_key = (user.kite_api_key_stored or "").strip() or os.getenv("KITE_API_KEY", "")
            if not api_key:
                logger.warning(
                    f"Skipping user {user.id} ({user.email}): "
                    "no API key configured (set KITE_API_KEY env var or save in profile)."
                )
                continue

            access_token = decrypt_token(user.kite_access_token_enc)
            if not access_token:
                logger.warning(f"Skipping user {user.id}: could not decrypt access token.")
                continue

            # Build the engine, apply token, validate, then start
            ue = engine_pool.get_or_create(user.id, api_key)
            try:
                from kiteconnect import KiteConnect
                ue.broker.kite = KiteConnect(api_key=api_key)
                ue.broker.config.api_key = api_key
                ue.broker.kite.set_access_token(access_token)
                ue.broker.kite.profile()   # validate — raises if token is stale
                ue.state.kite_auth_error = False
                ue.state.logs.append("[--:--:--] ✅ Kite session restored on startup.")
                ue.start(initial_mode)
                logger.info(
                    f"Restored session and started {initial_mode} engine "
                    f"for user {user.id} ({user.email})."
                )
            except Exception as e:
                logger.warning(
                    f"Could not restore session for user {user.id} ({user.email}): {e}"
                )
                ue.state.kite_auth_error = True
                ue.state.logs.append(
                    "[--:--:--] ⚠ Kite token invalid — open /profile to re-authenticate."
                )
    finally:
        db.close()


def _start_keepalive():
    """
    Prevent Render (and similar PaaS platforms) from spinning the server down
    due to inactivity by self-pinging the /health endpoint every 10 minutes.

    Only activates when RENDER_EXTERNAL_URL is set (Render injects this
    automatically).  Safe to leave enabled — on local dev the env var is
    absent so the thread simply exits immediately.

    For belt-and-suspenders, also point a free UptimeRobot monitor at
    https://<your-app>.onrender.com/health — that pings from outside every
    5 minutes and covers the window between self-pings.
    """
    base_url = os.getenv("RENDER_EXTERNAL_URL", "").rstrip("/")
    if not base_url:
        return   # not on Render, nothing to do

    ping_url = f"{base_url}/health"
    logger.info(f"Keep-alive: will ping {ping_url} every 10 minutes.")

    def _ping_loop():
        while True:
            time.sleep(600)   # 10 minutes
            try:
                with urllib.request.urlopen(ping_url, timeout=15) as resp:
                    logger.debug(f"Keep-alive ping → {resp.status}")
            except Exception as e:
                logger.warning(f"Keep-alive ping failed: {e}")

    t = threading.Thread(target=_ping_loop, daemon=True, name="KeepAlive")
    t.start()


def main():
    app_config = AppConfig()

    # Init DB first (creates tables + runs migrations)
    init_db()

    # Restore all users with valid today tokens
    _restore_all_active_users(app_config.mode)

    # Start the keep-alive pinger (no-op on local dev)
    _start_keepalive()

    # Start Flask — all per-user engines are managed by the pool singleton
    flask_app = create_app()
    logger.info(f"Dashboard live at http://{app_config.host}:{app_config.port}")
    flask_app.run(
        host=app_config.host,
        port=app_config.port,
        debug=False,
        use_reloader=False,
    )


if __name__ == "__main__":
    main()
