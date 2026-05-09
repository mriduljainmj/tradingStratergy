from flask import Blueprint, jsonify, render_template, request
from flask_jwt_extended import get_jwt_identity, jwt_required

from db.database import SessionLocal
from db.models import Strategy

strategy_bp = Blueprint("strategy", __name__)

# ── Default ORB strategy seeded for every new user ────────────────────────────
_ORB_DEFAULT_RULES = {
    "entry": {
        "conditions": [
            {"indicator": "PRICE", "condition": "CROSSES_ABOVE", "reference": "OR_HIGH", "action": "BUY_CALL"},
            {"indicator": "PRICE", "condition": "CROSSES_BELOW", "reference": "OR_LOW",  "action": "BUY_PUT"},
        ],
        "operator": "OR",
        "time_filter": {"start": "09:20", "end": "10:30"},
    },
    "exit": {
        "take_profit": {"type": "PREMIUM_POINTS", "value": 130},
        "stop_loss":   {"type": "FIB_TRAIL",      "value": 0.7},
        "time_exit":   {"time": "12:30"},
    },
    "position": {"lot_size": 25, "lots": 2},
}

_VALID_INDICATORS = {"PRICE","RSI_14","EMA_9","EMA_21","VWAP","OR_HIGH","OR_LOW"}
_VALID_CONDITIONS = {"CROSSES_ABOVE","CROSSES_BELOW","GREATER_THAN","LESS_THAN"}
_VALID_REFERENCES = {"OR_HIGH","OR_LOW","VWAP","NUMERIC"}
_VALID_ACTIONS    = {"BUY_CALL","BUY_PUT"}


def _uid():
    return int(get_jwt_identity())


def _bad(msg, code=400):
    return jsonify({"ok": False, "error": msg}), code


def _validate_rules(rules: dict) -> str | None:
    """Returns an error string if rules are invalid, else None."""
    if not isinstance(rules, dict):
        return "Rules must be a JSON object."
    entry = rules.get("entry", {})
    if not isinstance(entry.get("conditions"), list) or len(entry["conditions"]) == 0:
        return "At least one entry condition is required."
    for c in entry["conditions"]:
        if c.get("indicator") not in _VALID_INDICATORS:
            return f"Unknown indicator: {c.get('indicator')}"
        if c.get("condition") not in _VALID_CONDITIONS:
            return f"Unknown condition: {c.get('condition')}"
        if c.get("action") not in _VALID_ACTIONS:
            return f"Unknown action: {c.get('action')}"
    return None


# ── Pages ──────────────────────────────────────────────────────────────────────

@strategy_bp.route("/strategy-builder")
def builder_page():
    return render_template("strategy_builder.html")


# ── CRUD ───────────────────────────────────────────────────────────────────────

@strategy_bp.route("/api/strategies")
@jwt_required()
def list_strategies():
    db = SessionLocal()
    try:
        uid = _uid()
        strategies = db.query(Strategy).filter_by(user_id=uid).order_by(Strategy.updated_at.desc()).all()

        # ── Auto-seed ORB strategy for brand-new users ────────────────────────
        if not strategies:
            seed = Strategy(
                user_id=uid,
                name="ORB Breakout (Default)",
                description="Opening Range Breakout — buy CALL on OR High breakout, PUT on OR Low breakdown.",
                is_active=True,
            )
            seed.set_rules(_ORB_DEFAULT_RULES)
            db.add(seed)
            db.commit()
            db.refresh(seed)
            strategies = [seed]

        return jsonify({"ok": True, "strategies": [s.to_dict() for s in strategies]})
    finally:
        db.close()


@strategy_bp.route("/api/strategies", methods=["POST"])
@jwt_required()
def create_strategy():
    data  = request.get_json(silent=True) or {}
    name  = (data.get("name") or "").strip()
    rules = data.get("rules", {})

    if not name:
        return _bad("Strategy name is required.")
    err = _validate_rules(rules)
    if err:
        return _bad(err)

    db = SessionLocal()
    try:
        s = Strategy(user_id=_uid(), name=name, description=data.get("description",""))
        s.set_rules(rules)
        db.add(s)
        db.commit()
        db.refresh(s)
        return jsonify({"ok": True, "strategy": s.to_dict()}), 201
    except Exception as e:
        db.rollback()
        return _bad(str(e), 500)
    finally:
        db.close()


@strategy_bp.route("/api/strategies/<int:sid>")
@jwt_required()
def get_strategy(sid):
    db = SessionLocal()
    try:
        s = db.query(Strategy).filter_by(id=sid, user_id=_uid()).first()
        if not s:
            return _bad("Strategy not found.", 404)
        return jsonify({"ok": True, "strategy": s.to_dict()})
    finally:
        db.close()


@strategy_bp.route("/api/strategies/<int:sid>", methods=["PUT"])
@jwt_required()
def update_strategy(sid):
    data = request.get_json(silent=True) or {}
    db   = SessionLocal()
    try:
        s = db.query(Strategy).filter_by(id=sid, user_id=_uid()).first()
        if not s:
            return _bad("Strategy not found.", 404)

        if "name" in data:
            name = data["name"].strip()
            if not name:
                return _bad("Name cannot be empty.")
            s.name = name
        if "description" in data:
            s.description = data["description"]
        if "rules" in data:
            err = _validate_rules(data["rules"])
            if err:
                return _bad(err)
            s.set_rules(data["rules"])

        db.commit()
        db.refresh(s)
        return jsonify({"ok": True, "strategy": s.to_dict()})
    except Exception as e:
        db.rollback()
        return _bad(str(e), 500)
    finally:
        db.close()


@strategy_bp.route("/api/strategies/<int:sid>", methods=["DELETE"])
@jwt_required()
def delete_strategy(sid):
    db = SessionLocal()
    try:
        s = db.query(Strategy).filter_by(id=sid, user_id=_uid()).first()
        if not s:
            return _bad("Strategy not found.", 404)
        db.delete(s)
        db.commit()
        return jsonify({"ok": True})
    except Exception as e:
        db.rollback()
        return _bad(str(e), 500)
    finally:
        db.close()


@strategy_bp.route("/api/strategies/<int:sid>/activate", methods=["POST"])
@jwt_required()
def activate_strategy(sid):
    db = SessionLocal()
    try:
        uid = _uid()
        # Deactivate all
        db.query(Strategy).filter_by(user_id=uid).update({"is_active": False})
        # Activate the chosen one
        s = db.query(Strategy).filter_by(id=sid, user_id=uid).first()
        if not s:
            db.rollback()
            return _bad("Strategy not found.", 404)
        s.is_active = True
        db.commit()
        db.refresh(s)
        return jsonify({"ok": True, "strategy": s.to_dict()})
    except Exception as e:
        db.rollback()
        return _bad(str(e), 500)
    finally:
        db.close()
