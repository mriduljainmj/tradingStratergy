"""
Live / Paper Trading Entry Point
---------------------------------
Usage:
    python main.py

Set APP_MODE in .env (or Render env vars):
    BACKTEST  — replay today's 1-min data through the strategy
    PAPER     — live market feed, no real orders placed
    LIVE      — live market feed, real orders placed on Kite

Authentication on Render / cloud:
    The app starts without blocking for a login prompt.
    Visit  /auth  in your browser to authenticate with Kite each trading day.
    Alternatively set  KITE_ACCESS_TOKEN  in your Render env vars.
"""

import logging
import sys
import threading

from config.settings import AppConfig, TradingConfig
from core.state import BotState
from dashboard import create_app
from execution.broker import KiteBroker
from execution.historical_backtest import HistoricalBacktester
from execution.trading_engine import TradingEngine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# Shared mutable references so the /auth route can start the engine after login
_current_engine: TradingEngine = None
_engine_thread: threading.Thread = None


def main():
    global _current_engine, _engine_thread

    app_config = AppConfig()
    trading_config = TradingConfig()

    if not trading_config.api_key or not trading_config.api_secret:
        logger.error("KITE_API_KEY / KITE_API_SECRET not set in .env / env vars")
        sys.exit(1)

    state = BotState(app_mode=app_config.mode)
    broker = KiteBroker(trading_config)

    authenticated = broker.restore_session()

    if not authenticated:
        # Running on a server (Render) — don't block with input().
        # The user will authenticate via the /auth web page.
        import sys as _sys
        interactive = _sys.stdin.isatty()
        if interactive:
            # Local dev: fall back to terminal prompt
            print("═" * 60)
            print("  KITE AUTHENTICATION")
            print("═" * 60)
            print("1. Open:", broker.login_url())
            try:
                request_token = input("2. Paste request_token here: ").strip()
                if broker.authenticate(request_token):
                    authenticated = True
                else:
                    logger.error("Authentication failed. Exiting.")
                    sys.exit(1)
            except EOFError:
                pass  # non-interactive, fall through to web auth
        if not authenticated:
            logger.warning("No Kite session found. Visit /auth in your browser to log in.")
            state.logs.append("[--:--:--] ⚠ Not authenticated — visit /auth to log in")
    else:
        logger.info("Session restored — skipping login.")

    def _start_engine(mode: str):
        global _current_engine, _engine_thread
        engine = TradingEngine(trading_config, state, broker)
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
    )
    logger.info(f"Dashboard live at http://{app_config.host}:{app_config.port}")
    flask_app.run(host=app_config.host, port=app_config.port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
