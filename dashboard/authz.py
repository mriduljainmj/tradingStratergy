"""Admin authorization helpers.

ORB strategy / live-trading / backtest controls are restricted to admin users.
UI hiding alone is not enough — these decorators enforce it server-side so a
non-admin cannot drive the engine by calling the API directly.
"""
from functools import wraps

from flask import jsonify, request
from flask_jwt_extended import get_jwt_identity

from db.database import SessionLocal
from db.models import User


def current_user_is_admin() -> bool:
    try:
        uid = int(get_jwt_identity())
    except Exception:
        return False
    db = SessionLocal()
    try:
        user = db.get(User, uid)
        return bool(user and user.is_admin)
    finally:
        db.close()


def admin_required(fn):
    """Reject non-admin callers with 403. Place directly below @jwt_required()."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not current_user_is_admin():
            return jsonify({"ok": False, "error": "Admin access required."}), 403
        return fn(*args, **kwargs)
    return wrapper


def personal_execution_required(fn):
    """Personal paper/backtests are allowed; live controls retain admin policy."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        from core.engine_pool import engine_pool
        mode = ((request.get_json(silent=True) or {}).get('mode') if fn.__name__ == 'switch_mode' else
                engine_pool.get_or_create(int(get_jwt_identity())).state.app_mode)
        if str(mode).upper() == 'LIVE' and not current_user_is_admin():
            return jsonify(ok=False, error='Live trading requires an administrator account. Paper and Backtest are available on your account.'), 403
        return fn(*args, **kwargs)
    return wrapper
