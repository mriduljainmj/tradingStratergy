import os
from flask import Flask

from config.settings import TradingConfig
from core.state import BotState
from dashboard.routes import (
    dashboard_bp,
    register_state,
    register_mode_switcher,
    register_backtester,
    register_trading_config,
    register_broker,
    register_start_engine,
)


def create_app(state: BotState, mode_switcher=None, backtester=None,
               trading_config: TradingConfig = None, broker=None,
               start_engine_fn=None, initial_mode: str = "PAPER") -> Flask:
    template_dir = os.path.join(os.path.dirname(__file__), "templates")
    app = Flask(__name__, template_folder=template_dir)
    register_state(state)
    if mode_switcher:
        register_mode_switcher(mode_switcher)
    if backtester:
        register_backtester(backtester)
    if trading_config:
        register_trading_config(trading_config)
    if broker:
        register_broker(broker)
    if start_engine_fn:
        register_start_engine(start_engine_fn, initial_mode)
    app.register_blueprint(dashboard_bp)
    return app
