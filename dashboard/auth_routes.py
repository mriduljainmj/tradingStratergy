import datetime
import re

import bcrypt
from flask import Blueprint, jsonify, render_template, request
from flask_jwt_extended import create_access_token, get_jwt_identity, jwt_required

from db.database import SessionLocal
from db.models import User, Strategy

auth_bp = Blueprint("auth", __name__)

# Module-level references set by register_ helpers (injected from app.py)
_broker = None
_state  = None


def register_broker_ref(broker):
    global _broker
    _broker = broker


def register_state_ref(state):
    global _state
    _state = state

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# ── Default strategy seeded for every new user ─────────────────────────────────
_DEFAULT_ORB_STRATEGY = {
    "version": "1.0",
    "entry": {
        "conditions": [
            {
                "indicator": "PRICE",
                "condition": "CROSSES_ABOVE",
                "reference": "OR_HIGH",
                "action":    "BUY_CALL",
            },
            {
                "indicator": "PRICE",
                "condition": "CROSSES_BELOW",
                "reference": "OR_LOW",
                "action":    "BUY_PUT",
            },
        ],
        "operator":    "OR",
        "time_filter": {"start": "09:20", "end": "10:30"},
    },
    "exit": {
        "take_profit": {"type": "PREMIUM_POINTS", "value": 130},
        "stop_loss":   {"type": "FIB_TRAIL",       "value": 0.7},
        "time_exit":   {"time": "12:30"},
    },
    "position": {"lot_size": 25, "lots": 2},
}


def _seed_default_strategy(db, user_id: int):
    s = Strategy(
        user_id     = user_id,
        name        = "ORB Breakout (Default)",
        description = "Opening Range Breakout — buy CALL on OR High breakout, "
                      "PUT on OR Low breakdown. 130-pt target, 0.7 Fib trail SL, "
                      "EOD close at 12:30.",
        is_active   = True,
    )
    s.set_rules(_DEFAULT_ORB_STRATEGY)
    db.add(s)


def _bad(msg, code=400):
    return jsonify({"ok": False, "error": msg}), code


# ── Pages ──────────────────────────────────────────────────────────────────────

@auth_bp.route("/login")
def login_page():
    return render_template("login.html")


# ── API ────────────────────────────────────────────────────────────────────────

@auth_bp.route("/api/auth/register", methods=["POST"])
def register():
    data     = request.get_json(silent=True) or {}
    email    = (data.get("email")    or "").strip().lower()
    username = (data.get("username") or "").strip()
    password = (data.get("password") or "")

    if not email or not _EMAIL_RE.match(email):
        return _bad("Valid email is required.")
    if not username or len(username) < 3:
        return _bad("Username must be at least 3 characters.")
    if not password or len(password) < 8:
        return _bad("Password must be at least 8 characters.")

    db = SessionLocal()
    try:
        if db.query(User).filter_by(email=email).first():
            return _bad("Email already registered.")
        if db.query(User).filter_by(username=username).first():
            return _bad("Username already taken.")

        pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        user = User(email=email, username=username, password_hash=pw_hash)
        db.add(user)
        db.flush()  # get user.id before committing

        # Seed the default ORB strategy for every new account
        _seed_default_strategy(db, user.id)

        db.commit()
        db.refresh(user)

        token = create_access_token(identity=str(user.id))
        return jsonify({"ok": True, "token": token, "user": user.to_dict()}), 201
    except Exception as e:
        db.rollback()
        return _bad(str(e), 500)
    finally:
        db.close()


@auth_bp.route("/api/auth/login", methods=["POST"])
def login():
    data     = request.get_json(silent=True) or {}
    email    = (data.get("email")    or "").strip().lower()
    password = (data.get("password") or "")

    if not email or not password:
        return _bad("Email and password are required.")

    db = SessionLocal()
    try:
        user = db.query(User).filter_by(email=email).first()
        if not user or not bcrypt.checkpw(password.encode(), user.password_hash.encode()):
            return _bad("Invalid email or password.", 401)

        token = create_access_token(identity=str(user.id))
        return jsonify({"ok": True, "token": token, "user": user.to_dict()})
    finally:
        db.close()


@auth_bp.route("/api/auth/logout", methods=["POST"])
def logout():
    return jsonify({"ok": True})


@auth_bp.route("/api/auth/me")
@jwt_required()
def me():
    uid = int(get_jwt_identity())
    db  = SessionLocal()
    try:
        user = db.get(User, uid)
        if not user:
            return _bad("User not found.", 404)
        return jsonify({"ok": True, "user": user.to_dict()})
    finally:
        db.close()


@auth_bp.route("/profile")
def profile_page():
    return render_template("profile.html")


@auth_bp.route("/api/auth/profile", methods=["GET"])
@jwt_required()
def get_profile():
    uid = int(get_jwt_identity())
    db  = SessionLocal()
    try:
        user = db.get(User, uid)
        if not user:
            return _bad("User not found.", 404)
        return jsonify({"ok": True, "user": user.to_dict()})
    finally:
        db.close()


@auth_bp.route("/api/auth/profile", methods=["POST"])
@jwt_required()
def update_profile():
    uid  = int(get_jwt_identity())
    data = request.get_json(silent=True) or {}
    db   = SessionLocal()
    try:
        user = db.get(User, uid)
        if not user:
            return _bad("User not found.", 404)

        # Updateable fields
        if "display_name" in data:
            user.display_name = (data["display_name"] or "").strip()[:150] or None
        if "bio" in data:
            user.bio = (data["bio"] or "").strip()[:500] or None
        if "broker_id" in data:
            user.broker_id = (data["broker_id"] or "").strip()[:100] or None
        if "trade_confirm_modal" in data:
            user.trade_confirm_modal = bool(data["trade_confirm_modal"])
        if "photo_base64" in data:
            photo = data["photo_base64"]
            # Basic validation: must be a data URI or empty
            if photo and not photo.startswith("data:image/"):
                return _bad("Invalid photo format. Expected data:image/... base64 URI.")
            # Limit to ~500 KB (base64 is ~4/3 of raw, so 500KB base64 ≈ 375KB image)
            if photo and len(photo) > 700_000:
                return _bad("Photo too large. Max 500 KB.")
            user.photo_base64 = photo or None

        # Password change (optional)
        if data.get("new_password"):
            old_pw = data.get("old_password", "")
            if not bcrypt.checkpw(old_pw.encode(), user.password_hash.encode()):
                return _bad("Current password is incorrect.")
            new_pw = data["new_password"]
            if len(new_pw) < 8:
                return _bad("New password must be at least 8 characters.")
            user.password_hash = bcrypt.hashpw(new_pw.encode(), bcrypt.gensalt()).decode()

        db.commit()
        db.refresh(user)
        return jsonify({"ok": True, "user": user.to_dict()})
    except Exception as e:
        db.rollback()
        return _bad(str(e), 500)
    finally:
        db.close()


# ── Kite session management (serverless-safe encrypted token) ─────────────────

@auth_bp.route("/api/auth/kite-token", methods=["GET"])
@jwt_required()
def get_kite_token_status():
    """Return whether a valid Kite token exists for today."""
    uid = int(get_jwt_identity())
    db  = SessionLocal()
    try:
        user = db.get(User, uid)
        if not user:
            return _bad("User not found.", 404)
        today = datetime.date.today()
        has_token = bool(user.kite_access_token_enc and user.kite_token_date == today)
        return jsonify({
            "ok":              True,
            "has_token":       has_token,
            "token_date":      user.kite_token_date.isoformat() if user.kite_token_date else None,
            "kite_api_key":    user.kite_api_key_stored or "",
            "login_url":       _broker.login_url() if _broker else "",
        })
    finally:
        db.close()


@auth_bp.route("/api/auth/kite-token", methods=["POST"])
@jwt_required()
def save_kite_token():
    """
    Save an encrypted Kite access_token (and optional api_key) to the DB.
    Also immediately applies the token to the live broker so the engine
    can resume without a restart.
    """
    from execution.broker import encrypt_token

    uid  = int(get_jwt_identity())
    data = request.get_json(silent=True) or {}
    access_token = (data.get("access_token") or "").strip()
    api_key      = (data.get("api_key")      or "").strip()

    if not access_token:
        return _bad("access_token is required.")

    # 1️⃣ Validate ONLY when the user explicitly provides both api_key + access_token.
    #    If no api_key is given we cannot validate (the server may use a different key),
    #    so we skip validation and trust the user — the engine will surface a bad token
    #    naturally via the kite_auth_error flag.
    validated = False
    if _broker and api_key:
        try:
            from kiteconnect import KiteConnect
            tmp = KiteConnect(api_key=api_key)
            tmp.set_access_token(access_token)
            tmp.profile()   # raises if invalid
            validated = True
        except Exception as e:
            from execution.broker import is_kite_auth_error
            if is_kite_auth_error(e):
                return _bad("Kite rejected the token — check your API Key and Access Token.", 401)
            # Network / other error: save anyway, we can't validate right now
            validated = False

    # 2️⃣ Encrypt & persist
    db = SessionLocal()
    try:
        user = db.get(User, uid)
        if not user:
            return _bad("User not found.", 404)

        enc = encrypt_token(access_token)
        user.kite_access_token_enc = enc
        user.kite_token_date       = datetime.date.today()
        if api_key:
            user.kite_api_key_stored = api_key

        db.commit()
        db.refresh(user)

        # 3️⃣ Apply to live broker & clear auth error on state
        #    Priority order for api_key:
        #      a) what user typed now  →  b) what's stored in DB  →  c) server config key
        effective_api_key = api_key or user.kite_api_key_stored or ""
        if _broker:
            if effective_api_key:
                from kiteconnect import KiteConnect
                _broker.kite = KiteConnect(api_key=effective_api_key)
                _broker.config.api_key = effective_api_key  # keep config in sync
            _broker.kite.set_access_token(access_token)
            _broker._save_token(access_token)

            # Quick sanity-check — try profile() to confirm the token works NOW
            try:
                _broker.kite.profile()
                if _state:
                    _state.kite_auth_error = False
                    _state.logs.append("[--:--:--] ✅ Kite token applied — session restored.")
                validated = True
            except Exception as probe_err:
                import logging as _log
                _log.getLogger(__name__).warning(f"Token applied but profile() probe failed: {probe_err}")
                if _state:
                    _state.logs.append(f"[--:--:--] ⚠ Token saved but Kite probe failed: {probe_err}")
        elif _state:
            _state.kite_auth_error = False
            _state.logs.append("[--:--:--] ✅ Kite access token saved (broker not running).")

        return jsonify({
            "ok":        True,
            "validated": validated,
            "user":      user.to_dict(),
        })
    except Exception as e:
        db.rollback()
        return _bad(str(e), 500)
    finally:
        db.close()


@auth_bp.route("/api/auth/kite-token", methods=["DELETE"])
@jwt_required()
def clear_kite_token():
    """Remove the stored Kite token (e.g. end of day cleanup)."""
    uid = int(get_jwt_identity())
    db  = SessionLocal()
    try:
        user = db.get(User, uid)
        if not user:
            return _bad("User not found.", 404)
        user.kite_access_token_enc = None
        user.kite_token_date       = None
        db.commit()
        return jsonify({"ok": True})
    except Exception as e:
        db.rollback()
        return _bad(str(e), 500)
    finally:
        db.close()
