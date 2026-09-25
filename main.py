"""Start the Flask dashboard. Build frontend/ before running.

Broker sessions can be restored for the current trading day. Only administrators
who opted into background trading may auto-start, always in PAPER mode. LIVE
execution must be explicitly enabled through the dashboard after startup.
"""

import logging
import os
import threading
import time
import urllib.request

from config.settings import AppConfig
from core.engine_pool import engine_pool
from dashboard import create_app
from db.database import SessionLocal
from db.models import User
from execution.broker import _IST

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def _restore_all_active_users():
    """Queue restoration through the pool's single, paper-only startup policy."""
    if os.getenv('RESTORE_TRADING_SESSIONS', '1') == '0':
        return
    import datetime
    today = datetime.datetime.now(tz=_IST).date()
    with SessionLocal() as db:
        users = db.query(User).filter(
            User.kite_access_token_enc.isnot(None), User.kite_token_date == today,
        ).all()
        for user in users:
            engine_pool.get_or_create(user.id, (user.kite_api_key_stored or '').strip() or os.getenv('KITE_API_KEY', ''))


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

    # Create the app once; its factory initializes the database.
    flask_app = create_app()

    # Restore all users with valid today tokens
    _restore_all_active_users()

    # Start the keep-alive pinger (no-op on local dev)
    _start_keepalive()

    # Start Flask — all per-user engines are managed by the pool singleton
    logger.info(f"Dashboard live at http://{app_config.host}:{app_config.port}")
    flask_app.run(
        host=app_config.host,
        port=app_config.port,
        debug=False,
        use_reloader=False,
    )


if __name__ == "__main__":
    main()
