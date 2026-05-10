import datetime
import re

import bcrypt
from flask import Blueprint, jsonify, redirect, render_template, request, session
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

def _apply_token_to_broker(api_key: str, access_token: str):
    """Apply api_key + access_token to the running broker and clear auth error."""
    if not _broker:
        return
    from kiteconnect import KiteConnect
    _broker.kite = KiteConnect(api_key=api_key)
    _broker.config.api_key = api_key
    _broker.kite.set_access_token(access_token)
    _broker._save_token(access_token)
    if _state:
        _state.kite_auth_error = False
        _state.logs.append("[--:--:--] ✅ Kite session live.")


@auth_bp.route("/api/auth/kite-token", methods=["GET"])
@jwt_required()
def get_kite_token_status():
    """Return Kite credential status for the profile page."""
    uid = int(get_jwt_identity())
    db  = SessionLocal()
    try:
        user = db.get(User, uid)
        if not user:
            return _bad("User not found.", 404)
        today = datetime.date.today()
        has_token = bool(user.kite_access_token_enc and user.kite_token_date == today)
        return jsonify({
            "ok":             True,
            "has_token":      has_token,
            "has_api_secret": bool(user.kite_api_secret_enc),
            "token_date":     user.kite_token_date.isoformat() if user.kite_token_date else None,
            "kite_api_key":   user.kite_api_key_stored or "",
        })
    finally:
        db.close()


@auth_bp.route("/api/auth/kite-credentials", methods=["POST"])
@jwt_required()
def save_kite_credentials():
    """
    Save API Key + API Secret to the user profile (both encrypted in DB).
    These are permanent credentials — only need to be saved once.
    The access token is obtained separately via the OAuth flow (/kite/callback).
    """
    from execution.broker import encrypt_token

    uid  = int(get_jwt_identity())
    data = request.get_json(silent=True) or {}
    api_key    = (data.get("api_key")    or "").strip()
    api_secret = (data.get("api_secret") or "").strip()

    if not api_key:
        return _bad("api_key is required.")
    if not api_secret:
        return _bad("api_secret is required.")

    db = SessionLocal()
    try:
        user = db.get(User, uid)
        if not user:
            return _bad("User not found.", 404)

        user.kite_api_key_stored = api_key
        user.kite_api_secret_enc = encrypt_token(api_secret)
        db.commit()
        db.refresh(user)
        return jsonify({"ok": True, "user": user.to_dict()})
    except Exception as e:
        db.rollback()
        return _bad(str(e), 500)
    finally:
        db.close()


@auth_bp.route("/api/auth/kite-login-url")
@jwt_required()
def kite_login_url():
    """
    Initiate the Kite OAuth flow.
    Stores the user_id in the Flask session so /kite/callback knows who is logging in.
    Returns the Kite login URL to redirect the browser to.
    """
    uid = int(get_jwt_identity())
    db  = SessionLocal()
    try:
        user = db.get(User, uid)
        if not user:
            return _bad("User not found.", 404)
        if not user.kite_api_key_stored:
            return _bad("Save your API Key & Secret first.", 400)
        if not user.kite_api_secret_enc:
            return _bad("Save your API Secret first.", 400)

        from kiteconnect import KiteConnect
        kite = KiteConnect(api_key=user.kite_api_key_stored)
        login_url = kite.login_url()

        # Store uid in server-side session — the callback reads it back
        session["kite_oauth_uid"] = uid

        return jsonify({"ok": True, "login_url": login_url})
    finally:
        db.close()


@auth_bp.route("/kite/callback")
def kite_callback():
    """
    Kite OAuth redirect handler.
    Kite sends: /kite/callback?request_token=XXX&action=login&status=success
    We exchange request_token → access_token using the stored api_secret,
    save to DB, apply to broker, then redirect back to the profile page.

    Set this URL as the redirect URL in your Kite Developer app:
        http://127.0.0.1:8080/kite/callback        (local)
        https://your-app.onrender.com/kite/callback (production)
    """
    from execution.broker import decrypt_token, encrypt_token

    request_token = request.args.get("request_token", "").strip()
    status        = request.args.get("status", "")

    if status != "success" or not request_token:
        return redirect("/profile?kite_error=Login+cancelled+or+failed")

    uid = session.get("kite_oauth_uid")
    if not uid:
        return redirect("/profile?kite_error=Session+expired+—+open+profile+and+try+again")

    db = SessionLocal()
    try:
        user = db.get(User, uid)
        if not user or not user.kite_api_key_stored or not user.kite_api_secret_enc:
            return redirect("/profile?kite_error=API+credentials+not+found+—+save+them+first")

        api_key    = user.kite_api_key_stored
        api_secret = decrypt_token(user.kite_api_secret_enc)

        from kiteconnect import KiteConnect
        kite = KiteConnect(api_key=api_key)
        data = kite.generate_session(request_token, api_secret=api_secret)
        access_token = data["access_token"]

        # Encrypt & persist
        user.kite_access_token_enc = encrypt_token(access_token)
        user.kite_token_date       = datetime.date.today()
        db.commit()

        # Apply to running broker
        _apply_token_to_broker(api_key, access_token)

        session.pop("kite_oauth_uid", None)
        logger.info(f"Kite OAuth login successful for user {uid}.")
        return redirect("/profile?kite_ok=1")

    except Exception as e:
        logger.error(f"Kite OAuth callback failed: {e}")
        msg = str(e).replace(" ", "+")[:120]
        return redirect(f"/profile?kite_error={msg}")
    finally:
        db.close()


@auth_bp.route("/api/auth/kite-token", methods=["POST"])
@jwt_required()
def save_kite_token():
    """
    Manually save an access_token directly (fallback for users who already
    have a raw access_token, e.g. from a previous generate_session call).
    No validation — the engine surfaces bad tokens via kite_auth_error.
    """
    from execution.broker import encrypt_token

    uid  = int(get_jwt_identity())
    data = request.get_json(silent=True) or {}
    access_token = (data.get("access_token") or "").strip()

    if not access_token:
        return _bad("access_token is required.")

    db = SessionLocal()
    try:
        user = db.get(User, uid)
        if not user:
            return _bad("User not found.", 404)
        if not user.kite_api_key_stored:
            return _bad("Save your API Key & Secret first (they are required to use the token).")

        user.kite_access_token_enc = encrypt_token(access_token)
        user.kite_token_date       = datetime.date.today()
        db.commit()
        db.refresh(user)

        _apply_token_to_broker(user.kite_api_key_stored, access_token)
        if _state:
            _state.logs.append("[--:--:--] ✅ Kite access token saved and applied.")

        return jsonify({"ok": True, "user": user.to_dict()})
    except Exception as e:
        db.rollback()
        return _bad(str(e), 500)
    finally:
        db.close()


@auth_bp.route("/api/auth/kite-token", methods=["DELETE"])
@jwt_required()
def clear_kite_token():
    """Remove the stored Kite access token from DB and file cache."""
    import os
    uid = int(get_jwt_identity())
    db  = SessionLocal()
    try:
        user = db.get(User, uid)
        if not user:
            return _bad("User not found.", 404)
        user.kite_access_token_enc = None
        user.kite_token_date       = None
        db.commit()

        # Wipe file cache so DB and file stay in sync
        try:
            from execution.broker import _TOKEN_CACHE
            if os.path.exists(_TOKEN_CACHE):
                os.remove(_TOKEN_CACHE)
        except Exception:
            pass

        if _state:
            _state.kite_auth_error = True
            _state.logs.append("[--:--:--] 🗑 Kite token cleared — re-authenticate via /profile.")

        return jsonify({"ok": True})
    except Exception as e:
        db.rollback()
        return _bad(str(e), 500)
    finally:
        db.close()
