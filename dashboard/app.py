import os

from flask import Flask
from flask_jwt_extended import JWTManager

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
from dashboard.auth_routes     import auth_bp
from dashboard.analytics_routes import analytics_bp
from dashboard.strategy_routes  import strategy_bp
from db.database import init_db


def create_app(state: BotState, mode_switcher=None, backtester=None,
               trading_config: TradingConfig = None, broker=None,
               start_engine_fn=None, initial_mode: str = "PAPER") -> Flask:
    template_dir = os.path.join(os.path.dirname(__file__), "templates")
    app = Flask(__name__, template_folder=template_dir)

    # JWT config — use a strong random secret in production
    app.config["JWT_SECRET_KEY"] = os.getenv("JWT_SECRET_KEY", "orb-dev-secret-change-in-prod")
    app.config["JWT_ACCESS_TOKEN_EXPIRES"] = False  # 24h tokens; set to timedelta(hours=24) for prod
    JWTManager(app)

    # Init DB tables
    init_db()

    # Register state + callbacks
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

    # Register blueprints
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(analytics_bp)
    app.register_blueprint(strategy_bp)

    return app
