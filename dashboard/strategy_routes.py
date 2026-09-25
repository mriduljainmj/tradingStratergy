from execution.order_safety import has_exposure
from flask import Blueprint, jsonify, request
from werkzeug.exceptions import HTTPException
from flask_jwt_extended import get_jwt_identity, jwt_required

from db.database import SessionLocal
from db.models import Strategy
from dashboard.authz import admin_required
from dashboard.api_support import validate_strategy

strategy_bp = Blueprint("strategy", __name__)

# ── Default ORB strategy seeded for every new user ────────────────────────────
from config.strategy_defaults import default_options_rules

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
            seed.set_rules(default_options_rules())
            db.add(seed)
            db.commit()
            db.refresh(seed)
            strategies = [seed]

        return jsonify({"ok": True, "strategies": [s.to_dict() for s in strategies]})
    finally:
        db.close()


@strategy_bp.route("/api/strategies", methods=["POST"])
@jwt_required()
@admin_required
def create_strategy():
    data  = request.get_json(silent=True) or {}
    name  = (data.get("name") or "").strip()
    rules = data.get("rules", {})
    validate_strategy(data)

    if not name:
        return _bad("Strategy name is required.")
    itype = (data.get("instrument_type") or "OPTIONS").upper()
    if itype not in ("OPTIONS", "EQUITY"):
        return _bad("instrument_type must be OPTIONS or EQUITY")
    # ORB-style rule validation applies to options strategies only; equity
    # rules are the flat {qty, sl_pct, tgt_pct, ema_fast, ...} schema.
    if itype == "OPTIONS" and isinstance(rules, dict) and "entry" in rules:
        # Legacy ORB conditions schema — validate it. Flat param schema
        # (target_pts / fib_trail / …) from the Builder is accepted as-is.
        err = _validate_rules(rules)
        if err:
            return _bad(err)

    db = SessionLocal()
    try:
        s = Strategy(user_id=_uid(), name=name, description=data.get("description",""),
                     instrument_type=itype,
                     symbol=(data.get("symbol") or ("NIFTY 50" if itype == "OPTIONS" else "RELIANCE")).upper(),
                     engine_type=(data.get("engine_type") or ("ORB" if itype == "OPTIONS" else "EQUITY_ORB")).upper())
        s.set_rules(rules)
        db.add(s)
        db.commit()
        db.refresh(s)
        return jsonify({"ok": True, "strategy": s.to_dict()}), 201
    except HTTPException:
        raise
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
@admin_required
def update_strategy(sid):
    data = request.get_json(silent=True) or {}
    db   = SessionLocal()
    try:
        s = db.query(Strategy).filter_by(id=sid, user_id=_uid()).first()
        if not s:
            return _bad("Strategy not found.", 404)

        from core.engine_pool import engine_pool
        ue = engine_pool.get(_uid())
        if s.is_running or (ue and ue.state.active_strategy_id == sid and has_exposure(ue)):
            return _bad('Stop the strategy and close its position before editing.', 409)
        validate_strategy(data, s)
        if 'instrument_type' in data and data['instrument_type'] != s.instrument_type:
            return _bad('Create a new strategy to change instrument type.')
        if "name" in data:
            name = data["name"].strip()
            if not name:
                return _bad("Name cannot be empty.")
            s.name = name
        if "description" in data:
            s.description = data["description"]
        if "rules" in data:
            r = data["rules"]
            if (s.instrument_type or "OPTIONS") == "OPTIONS" and isinstance(r, dict) and "entry" in r:
                err = _validate_rules(r)
                if err:
                    return _bad(err)
            s.set_rules(r)
        if "symbol" in data:
            s.symbol = (data["symbol"] or s.symbol or "").upper()
        if "engine_type" in data:
            s.engine_type = (data["engine_type"] or s.engine_type or "").upper()

        db.commit()
        db.refresh(s)
        return jsonify({"ok": True, "strategy": s.to_dict()})
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        return _bad(str(e), 500)
    finally:
        db.close()


@strategy_bp.route("/api/strategies/<int:sid>", methods=["DELETE"])
@jwt_required()
@admin_required
def delete_strategy(sid):
    db = SessionLocal()
    try:
        s = db.query(Strategy).filter_by(id=sid, user_id=_uid()).first()
        if not s:
            return _bad("Strategy not found.", 404)
        from core.engine_pool import engine_pool
        ue = engine_pool.get(_uid())
        if s.is_running or (ue and ue.state.active_strategy_id == sid and has_exposure(ue)):
            return _bad('Stop the strategy and close its position before deleting.', 409)
        db.delete(s)
        db.commit()
        return jsonify({"ok": True})
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        return _bad(str(e), 500)
    finally:
        db.close()


# ── Phase-2: run/stop equity strategies as parallel engines ──────────────────

@strategy_bp.route("/api/strategies/<int:sid>/start", methods=["POST"])
@jwt_required()
@admin_required
def start_strategy_engine(sid):
    """Start the per-strategy equity engine (paper by default; {"mode":"live"})."""
    from core.engine_pool import engine_pool
    uid  = _uid()
    body = request.get_json(silent=True) or {}
    mode = (body.get('mode') or 'paper').lower()
    if mode not in ('paper', 'live'):
        return _bad('mode must be paper or live.')
    live = mode == 'live'
    db = SessionLocal()
    try:
        s = db.query(Strategy).filter_by(id=sid, user_id=uid).first()
        if not s:
            return _bad("Strategy not found", 404)
        if (s.instrument_type or "OPTIONS") != "EQUITY":
            return _bad("Only EQUITY strategies run as parallel engines — "
                        "the OPTIONS ORB strategy runs via the main engine (Take Trades).")
        ue = engine_pool.get_or_create(uid)
        if ue.state.kite_auth_error or not ue.broker.kite.access_token:
            return _bad('Connect Kite before starting a strategy.', 409)
        validate_strategy(s.to_dict())
        snap = ue.start_equity_strategy(s.to_dict(), paper=not live)
        s.is_running = True
        s.run_mode   = "LIVE" if live else "PAPER"
        db.commit()
        return jsonify({"ok": True, "engine": snap})
    finally:
        db.close()


@strategy_bp.route("/api/strategies/<int:sid>/stop", methods=["POST"])
@jwt_required()
@admin_required
def stop_strategy_engine(sid):
    from core.engine_pool import engine_pool
    uid = _uid()
    ue  = engine_pool.get_or_create(uid)
    eng = getattr(ue, 'equity_engines', {}).get(sid)
    if eng and (eng.in_pos or getattr(eng, 'execution_blocked', False)):
        return _bad('Exit the position and reconcile pending orders before stopping.', 409)
    with SessionLocal() as lookup:
        if not lookup.query(Strategy).filter_by(id=sid, user_id=uid).first():
            return _bad('Strategy not found.', 404)
    ue.stop_equity_strategy(sid)
    db = SessionLocal()
    try:
        s = db.query(Strategy).filter_by(id=sid, user_id=uid).first()
        if s:
            s.is_running = False
            s.run_mode   = None
            db.commit()
    finally:
        db.close()
    return jsonify({"ok": True})


@strategy_bp.route("/api/strategies/<int:sid>/manual", methods=["POST"])
@jwt_required()
@admin_required
def manual_equity(sid):
    """Manually enter/exit a running equity strategy. Body: {"action":"enter"|"exit"}"""
    from core.engine_pool import engine_pool
    action = ((request.get_json(silent=True) or {}).get("action") or "").upper()
    if action not in ("ENTER", "EXIT"):
        return _bad("action must be enter or exit")
    ue = engine_pool.get_or_create(_uid())
    if ue.manual_equity_action(sid, action):
        return jsonify({"ok": True, "queued": action})
    return _bad("Strategy is not running — press Run first.", 400)


@strategy_bp.route("/api/strategies/running")
@jwt_required()
def running_strategies():
    """Live snapshots of all equity strategy engines for this user."""
    from core.engine_pool import engine_pool
    ue = engine_pool.get_or_create(_uid())
    return jsonify({"ok": True, "engines": ue.equity_snapshots()})


@strategy_bp.route("/api/strategies/<int:sid>/activate", methods=["POST"])
@jwt_required()
@admin_required
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
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        return _bad(str(e), 500)
    finally:
        db.close()
