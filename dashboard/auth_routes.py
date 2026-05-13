import datetime
import logging
import os
import re

import bcrypt
from flask import Blueprint, jsonify, redirect, render_template, request, session
from flask_jwt_extended import create_access_token, get_jwt_identity, jwt_required

from db.database import SessionLocal
from db.models import User, Strategy

logger = logging.getLogger(__name__)

auth_bp = Blueprint("auth", __name__)

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# ── Kite credential resolution ─────────────────────────────────────────────────
# Users can optionally store their own API key/secret (for power users who have
# their own ₹500/month Kite Connect subscription).  If they haven't, we fall
# back to the app's global credentials from env vars so they can connect with
# just a "Login with Zerodha" click — no personal API subscription needed.

def _resolve_kite_api_key(user: "User") -> str:
    """Return the effective Kite API key for this user (own key > app global)."""
    return (user.kite_api_key_stored or "").strip() or os.getenv("KITE_API_KEY", "")


def _resolve_kite_api_secret(user: "User") -> str:
    """Return the effective Kite API secret for this user (own secret > app global)."""
    if user.kite_api_secret_enc:
        from execution.broker import decrypt_token
        secret = decrypt_token(user.kite_api_secret_enc)
        if secret:
            return secret
    return os.getenv("KITE_API_SECRET", "")

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

        # First-ever user automatically becomes admin
        is_first_user = db.query(User).count() == 0

        pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        user = User(
            email=email,
            username=username,
            password_hash=pw_hash,
            is_admin=is_first_user,
        )
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

        # ── Restore today's Kite session if the user already authenticated ─────
        # This means engine starts automatically on app login — no extra step needed
        # unless the daily token has expired (Kite tokens expire at midnight).
        today = datetime.date.today()
        if user.kite_access_token_enc and user.kite_token_date == today:
            try:
                from execution.broker import decrypt_token
                access_token = decrypt_token(user.kite_access_token_enc)
                api_key      = _resolve_kite_api_key(user)
                if access_token and api_key:
                    _apply_token_to_broker(user.id, api_key, access_token)
                    logger.info(f"Kite session auto-restored on login for user {user.id}.")
            except Exception as e:
                logger.warning(f"Auto-restore Kite session failed for user {user.id}: {e}")

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

def _apply_token_to_broker(user_id: int, api_key: str, access_token: str):
    """
    Apply api_key + access_token to the user's engine and start it if needed.
    Also fetches the Kite profile to persist the Zerodha client ID as broker_id.
    """
    from core.engine_pool import engine_pool
    from kiteconnect import KiteConnect

    ue = engine_pool.get_or_create(user_id, api_key)
    ue.broker.kite = KiteConnect(api_key=api_key)
    ue.broker.config.api_key = api_key
    ue.broker.kite.set_access_token(access_token)
    ue.state.kite_auth_error = False
    ue.state.logs.append("[--:--:--] ✅ Kite session live.")

    # Fetch Kite profile → persist Zerodha client ID as broker_id
    try:
        profile = ue.broker.kite.profile()
        kite_client_id = profile.get("user_id", "")
        if kite_client_id:
            from db.database import SessionLocal
            from db.models import User as _User
            _db = SessionLocal()
            try:
                _u = _db.get(_User, user_id)
                if _u:
                    _u.broker_id = kite_client_id
                    _db.commit()
                    logger.info(
                        f"Kite profile fetched for user {user_id}: "
                        f"client_id={kite_client_id}"
                    )
            finally:
                _db.close()
    except Exception as e:
        logger.warning(f"Could not fetch Kite profile for user {user_id}: {e}")

    # Start the engine in PAPER mode if it isn't already running
    if not ue.is_running:
        ue.start("PAPER")


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


@auth_bp.route("/api/auth/kite-info", methods=["GET"])
@jwt_required()
def kite_info():
    """
    Return live Kite account info for the profile page:
      - Zerodha client ID and account name (from kite.profile())
      - Available balance and used margin (from kite.margins())
    Only works when the user has an active Kite session today.
    """
    uid = int(get_jwt_identity())
    from core.engine_pool import engine_pool
    ue = engine_pool.get(uid)

    if not ue or ue.state.kite_auth_error:
        return _bad("Kite not authenticated — please log in via the Kite button.", 401)

    try:
        profile = ue.broker.kite.profile()
        funds   = ue.broker.get_funds()
        return jsonify({
            "ok":        True,
            "client_id": profile.get("user_id",   ""),
            "name":      profile.get("user_name",  ""),
            "email":     profile.get("email",      ""),
            "broker":    profile.get("broker",     "ZERODHA"),
            "balance":   funds,
        })
    except Exception as e:
        from execution.broker import is_kite_auth_error
        if is_kite_auth_error(e) and ue:
            ue.state.kite_auth_error = True
        return _bad(str(e), 500)


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
    Uses the user's own API key if they've stored one; otherwise falls back to
    the app's global KITE_API_KEY env var — so users don't need a personal
    ₹500/month Kite Connect subscription.
    Stores the user_id in the Flask session so /kite/callback knows who is logging in.
    """
    uid = int(get_jwt_identity())
    db  = SessionLocal()
    try:
        user = db.get(User, uid)
        if not user:
            return _bad("User not found.", 404)

        api_key    = _resolve_kite_api_key(user)
        api_secret = _resolve_kite_api_secret(user)
        if not api_key:
            return _bad(
                "No Kite API key configured. "
                "Either save your own API Key in your profile, or ask the app "
                "administrator to set the KITE_API_KEY environment variable.", 400
            )
        if not api_secret:
            return _bad(
                "No Kite API secret configured. "
                "Either save your own API Secret in your profile, or ask the app "
                "administrator to set the KITE_API_SECRET environment variable.", 400
            )

        from kiteconnect import KiteConnect
        kite = KiteConnect(api_key=api_key)
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
        # No server session — likely triggered from a different server (e.g. local dev
        # while the Kite redirect URL points to production).  The request_token is still
        # unconsumed, so surface it so the user can paste it into their local server's
        # "Exchange request_token" field rather than showing a useless error.
        return redirect(f"/profile?kite_pending={request_token}")

    db = SessionLocal()
    try:
        user = db.get(User, uid)
        if not user:
            return redirect("/profile?kite_error=User+not+found")

        # Use user's own credentials if stored, otherwise fall back to app globals
        api_key    = _resolve_kite_api_key(user)
        api_secret = _resolve_kite_api_secret(user)
        if not api_key or not api_secret:
            return redirect("/profile?kite_error=API+credentials+not+configured")

        from kiteconnect import KiteConnect
        kite = KiteConnect(api_key=api_key)
        data = kite.generate_session(request_token, api_secret=api_secret)
        access_token = data["access_token"]

        # Encrypt & persist the access token + record which api_key was used
        user.kite_access_token_enc = encrypt_token(access_token)
        user.kite_token_date       = datetime.date.today()
        # Store the resolved api_key so restore_from_db uses the right one
        if not user.kite_api_key_stored:
            user.kite_api_key_stored = api_key
        db.commit()

        # Apply to running broker and start engine
        _apply_token_to_broker(uid, api_key, access_token)

        session.pop("kite_oauth_uid", None)
        logger.info(f"Kite OAuth login successful for user {uid}.")
        return redirect("/profile?kite_ok=1")

    except Exception as e:
        logger.error(f"Kite OAuth callback failed: {e}")
        msg = str(e).replace(" ", "+")[:120]
        return redirect(f"/profile?kite_error={msg}")
    finally:
        db.close()


@auth_bp.route("/api/auth/kite-exchange", methods=["POST"])
@jwt_required()
def exchange_kite_token():
    """
    Exchange a request_token (from the Kite OAuth redirect URL) for an
    access_token using the stored api_secret.

    Useful for local development where the Kite redirect URL points to the
    production server (/kite/callback).  The user copies the request_token
    query-param from the production redirect and pastes it here — the local
    server does the exchange itself without needing its own callback URL.

    Falls back to the app's global KITE_API_KEY / KITE_API_SECRET env vars
    if the user hasn't stored their own credentials.
    """
    from execution.broker import encrypt_token

    uid  = int(get_jwt_identity())
    data = request.get_json(silent=True) or {}
    request_token = (data.get("request_token") or "").strip()

    if not request_token:
        return _bad("request_token is required.")

    db = SessionLocal()
    try:
        user = db.get(User, uid)
        if not user:
            return _bad("User not found.", 404)

        api_key    = _resolve_kite_api_key(user)
        api_secret = _resolve_kite_api_secret(user)
        if not api_key:
            return _bad("No API key configured — save your credentials or ask the administrator to set KITE_API_KEY.")
        if not api_secret:
            return _bad("No API secret configured — save your credentials or ask the administrator to set KITE_API_SECRET.")

        from kiteconnect import KiteConnect
        kite      = KiteConnect(api_key=api_key)
        kite_data = kite.generate_session(request_token, api_secret=api_secret)
        access_token = kite_data["access_token"]

        user.kite_access_token_enc = encrypt_token(access_token)
        user.kite_token_date       = datetime.date.today()
        if not user.kite_api_key_stored:
            user.kite_api_key_stored = api_key
        db.commit()
        db.refresh(user)

        _apply_token_to_broker(uid, api_key, access_token)

        return jsonify({"ok": True, "user": user.to_dict()})
    except Exception as e:
        db.rollback()
        return _bad(str(e), 500)
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

        api_key = _resolve_kite_api_key(user)
        if not api_key:
            return _bad(
                "No Kite API key configured. "
                "Either save your own API Key in your profile, or ask the app "
                "administrator to set the KITE_API_KEY environment variable."
            )

        user.kite_access_token_enc = encrypt_token(access_token)
        user.kite_token_date       = datetime.date.today()
        if not user.kite_api_key_stored:
            user.kite_api_key_stored = api_key
        db.commit()
        db.refresh(user)

        _apply_token_to_broker(uid, api_key, access_token)

        return jsonify({"ok": True, "user": user.to_dict()})
    except Exception as e:
        db.rollback()
        return _bad(str(e), 500)
    finally:
        db.close()


@auth_bp.route("/api/auth/account", methods=["DELETE"])
@jwt_required()
def delete_account():
    """Permanently delete the calling user's account and all associated data."""
    uid = int(get_jwt_identity())
    db  = SessionLocal()
    try:
        user = db.get(User, uid)
        if not user:
            return _bad("User not found.", 404)

        # Stop and remove the engine from the pool before deleting
        from core.engine_pool import engine_pool
        engine_pool.remove(uid)

        db.delete(user)
        db.commit()
        return jsonify({"ok": True})
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

        # Stop and remove the user's engine from the pool — they must re-auth
        from core.engine_pool import engine_pool
        engine_pool.remove(uid)

        return jsonify({"ok": True})
    except Exception as e:
        db.rollback()
        return _bad(str(e), 500)
    finally:
        db.close()
