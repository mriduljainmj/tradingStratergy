import logging
import os

import bcrypt
from flask import Flask
from flask_jwt_extended import JWTManager

from dashboard.routes       import dashboard_bp
from dashboard.auth_routes  import auth_bp, _seed_default_strategy
from dashboard.analytics_routes import analytics_bp
from dashboard.strategy_routes  import strategy_bp
from dashboard.screener_routes  import screener_bp
from db.database import init_db, SessionLocal
from db.models import User

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
            # Ensure the default user always has admin rights (safe to re-run)
            if not existing.is_admin:
                existing.is_admin = True
                db.commit()
                logger.info(f"Default user promoted to admin: {email}")
            else:
                logger.info(f"Default user already exists: {email}")
            return

        username = email.split("@")[0]
        base, counter = username, 1
        while db.query(User).filter_by(username=username).first():
            username = f"{base}{counter}"
            counter += 1

        pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        user = User(email=email, username=username, password_hash=pw_hash, is_admin=True)
        db.add(user)
        db.flush()
        _seed_default_strategy(db, user.id)
        db.commit()
        logger.info(f"Default user created (admin) — email: {email}  username: {username}")
    except Exception as e:
        db.rollback()
        logger.warning(f"Could not create default user: {e}")
    finally:
        db.close()


def create_app() -> Flask:
    """
    Create and configure the Flask application.

    All per-user trading state (BotState, KiteBroker, TradingEngine) is managed
    by the EnginePool singleton in core/engine_pool.py.  Routes look up the
    calling user's engine via JWT identity — no global state is injected here.
    """
    template_dir = os.path.join(os.path.dirname(__file__), "templates")
    app = Flask(__name__, template_folder=template_dir)

    # JWT + Flask session secret
    _secret = os.getenv("JWT_SECRET_KEY", "orb-dev-secret-change-in-prod")
    app.config["SECRET_KEY"]               = _secret
    app.config["JWT_SECRET_KEY"]           = _secret
    app.config["JWT_ACCESS_TOKEN_EXPIRES"] = False
    JWTManager(app)

    # Init DB tables + migrations, then seed the default local user (if configured)
    init_db()
    _ensure_default_user()

    # Register blueprints — no register_* calls needed; blueprints use the pool singleton
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(analytics_bp)
    app.register_blueprint(strategy_bp)
    app.register_blueprint(screener_bp)

    return app
