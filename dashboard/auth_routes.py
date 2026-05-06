import re

import bcrypt
from flask import Blueprint, jsonify, render_template, request
from flask_jwt_extended import create_access_token, get_jwt_identity, jwt_required

from db.database import SessionLocal
from db.models import User

auth_bp = Blueprint("auth", __name__)

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _bad(msg, code=400):
    return jsonify({"ok": False, "error": msg}), code


# ── Pages ──────────────────────────────────────────────────────────────────────

@auth_bp.route("/login")
def login_page():
    return render_template("login.html")


# ── API ────────────────────────────────────────────────────────────────────────

@auth_bp.route("/api/auth/register", methods=["POST"])
def register():
    data = request.get_json(silent=True) or {}
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
        db.commit()
        db.refresh(user)

        token = create_access_token(identity=str(user.id))
        return jsonify({"ok": True, "token": token, "user": user.to_dict()}), 201
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
    # JWT is stateless — client drops the token
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
