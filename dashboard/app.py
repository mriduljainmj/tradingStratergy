import logging
import os

import bcrypt
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
from dashboard.auth_routes      import auth_bp, _seed_default_strategy
from dashboard.analytics_routes import analytics_bp
from dashboard.strategy_routes  import strategy_bp
from db.database import init_db, SessionLocal
from db.models import Strategy, User

logger = logging.getLogger(__name__)


def _ensure_default_user():
    """
    If DEFAULT_USER_EMAIL / DEFAULT_USER_PASSWORD are set in the environment,
    create that user (and their default ORB strategy) if it doesn't already exist.
    Useful for local development — no manual registration required.
    """
    email    = os.getenv("DEFAULT_USER_EMAIL", "").strip().lower()
    password = os.getenv("DEFAULT_USER_PASSWORD", "").strip()
    if not email or not password:
        return

    db = SessionLocal()
    try:
        existing = db.query(User).filter_by(email=email).first()
        if existing:
            logger.info(f"Default user already exists: {email}")
            return

        username = email.split("@")[0]
        # Make username unique if it already exists
        base = username
        counter = 1
        while db.query(User).filter_by(username=username).first():
            username = f"{base}{counter}"
            counter += 1

        pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        user = User(email=email, username=username, password_hash=pw_hash)
        db.add(user)
        db.flush()
        _seed_default_strategy(db, user.id)
        db.commit()
        logger.info(f"Default user created — email: {email}  username: {username}")
    except Exception as e:
        db.rollback()
        logger.warning(f"Could not create default user: {e}")
    finally:
        db.close()


def create_app(state: BotState, mode_switcher=None, backtester=None,
               trading_config: TradingConfig = None, broker=None,
               start_engine_fn=None, initial_mode: str = "PAPER") -> Flask:
    template_dir = os.path.join(os.path.dirname(__file__), "templates")
    app = Flask(__name__, template_folder=template_dir)

    # JWT — use a strong random secret in production (set JWT_SECRET_KEY env var)
    app.config["JWT_SECRET_KEY"] = os.getenv("JWT_SECRET_KEY", "orb-dev-secret-change-in-prod")
    app.config["JWT_ACCESS_TOKEN_EXPIRES"] = False
    JWTManager(app)

    # Init DB tables then seed the default local user (if configured)
    init_db()
    _ensure_default_user()

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
