"""
Live / Paper Trading Entry Point
---------------------------------
Usage:
    python main.py

Set APP_MODE in .env:
    BACKTEST  — replay today's 1-min data through the strategy
    PAPER     — live market feed, no real orders placed
    LIVE      — live market feed, real orders placed on Kite
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


def _log_to_state(state: BotState, msg: str):
    import datetime
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    entry = f"[{ts}] {msg}"
    logger.info(msg)
    state.logs.append(entry)


def main():
    app_config = AppConfig()
    trading_config = TradingConfig()

    if not trading_config.api_key or not trading_config.api_secret:
        logger.error("KITE_API_KEY / KITE_API_SECRET not set in .env")
        sys.exit(1)

    state = BotState(app_mode=app_config.mode)
    broker = KiteBroker(trading_config)

    if not broker.restore_session():
        print("═" * 60)
        print("  KITE AUTHENTICATION")
        print("═" * 60)
        print("1. Open:", broker.login_url())
        request_token = input("2. Paste request_token here: ").strip()
        if not broker.authenticate(request_token):
            logger.error("Authentication failed. Exiting.")
            sys.exit(1)
    else:
        logger.info("Session restored — skipping login.")

    current_engine: TradingEngine = None
    engine_thread: threading.Thread = None

    def _start_engine(mode: str):
        nonlocal current_engine, engine_thread
        engine = TradingEngine(trading_config, state, broker)
        current_engine = engine

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
        engine_thread = t
        t.start()

    def switch_mode(new_mode: str):
        nonlocal current_engine
        logger.info(f"Mode switch requested: {state.app_mode} → {new_mode}")
        if current_engine:
            current_engine.stop()
        state.reset(new_mode)
        _start_engine(new_mode)

    _start_engine(app_config.mode)

    backtester = HistoricalBacktester(trading_config, broker)
    flask_app = create_app(state, mode_switcher=switch_mode, backtester=backtester)
    logger.info(f"Dashboard live at http://{app_config.host}:{app_config.port}")
    flask_app.run(host=app_config.host, port=app_config.port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
