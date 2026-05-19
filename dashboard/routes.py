import datetime
import json
import logging

from flask import Blueprint, jsonify, render_template, request
from flask_jwt_extended import get_jwt_identity, jwt_required

from config.config_utils import config_to_dict, apply_config_dict
from core.engine_pool import engine_pool

logger = logging.getLogger(__name__)

dashboard_bp = Blueprint("dashboard", __name__)

VALID_MODES = {"BACKTEST", "PAPER", "LIVE"}


# ── Internal helpers ──────────────────────────────────────────────────────────

def _uid() -> int:
    return int(get_jwt_identity())


def _ue():
    """Return the calling user's UserEngine (creates one if needed)."""
    return engine_pool.get_or_create(_uid())



# ── Pages ─────────────────────────────────────────────────────────────────────

@dashboard_bp.route("/")
def index():
    # mode is no longer needed server-side — JS fetches /api/state after load
    return render_template("dashboard.html", mode="PAPER")


@dashboard_bp.route("/health")
def health():
    """
    Lightweight health-check endpoint.
    Used by the keep-alive self-ping and external monitors (UptimeRobot, etc.)
    to prevent Render from spinning the server down while engines are active.
    """
    active = len(engine_pool.all_engines())
    trading = sum(
        1 for ue in engine_pool.all_engines()
        if ue.is_running
    )
    return jsonify({"ok": True, "engines": active, "running": trading})


# ── Trading state ─────────────────────────────────────────────────────────────

@dashboard_bp.route("/api/state")
@jwt_required()
def get_state():
    ue = _ue()
    d = ue.state.to_dict()
    # Expose the configured trade quantity so the UI can display it directly
    # rather than trying to back-calculate it from live_pnl (which fluctuates).
    d["qty"] = ue.config.qty
    return jsonify(d)


@dashboard_bp.route("/api/balance")
@jwt_required()
def get_balance():
    ue = _ue()
    if ue.state.app_mode == "LIVE":
        funds = ue.broker.get_funds()
        ue.state.balance = funds["available"]
        return jsonify(funds)
    bal = ue.state.balance
    return jsonify({"available": bal, "used": 0.0, "total": bal})


# ── Settings ──────────────────────────────────────────────────────────────────

@dashboard_bp.route("/api/settings", methods=["GET"])
@jwt_required()
def get_settings():
    """Return saved settings for a specific mode (or the current mode).

    Query param: ?mode=BACKTEST|PAPER|LIVE
    """
    from config.config_utils import get_mode_settings
    from db.database import SessionLocal
    from db.models import User

    uid  = _uid()
    ue   = _ue()
    mode = (request.args.get("mode", "") or "").upper()
    if mode not in ("BACKTEST", "PAPER", "LIVE"):
        mode = ue.state.app_mode   # default: current running mode

    db = SessionLocal()
    try:
        user     = db.get(User, uid)
        raw_json = user.settings_json if user else ""
    finally:
        db.close()

    mode_data = get_mode_settings(raw_json, mode)

    # Fill gaps with live config defaults (so UI always has something to show)
    result = {**config_to_dict(ue.config), **mode_data}
    result["trade_direction"] = mode_data.get(
        "trade_direction",
        getattr(ue.state, "trade_direction", "BOTH"),
    )
    return jsonify(result)


@dashboard_bp.route("/api/settings", methods=["POST"])
@jwt_required()
def update_settings():
    """Save settings for a specific mode.

    Body: { "mode": "PAPER", ...config fields..., "trade_direction": "CALL" }
    Settings are saved per-mode in settings_json.
    If the saved mode matches the running mode the live engine is updated too.
    """
    from config.config_utils import get_mode_settings, set_mode_settings
    from db.database import SessionLocal
    from db.models import User

    uid  = _uid()
    ue   = engine_pool.get_or_create(uid)
    data = request.json or {}

    mode = (data.pop("mode", None) or ue.state.app_mode).upper()
    if mode not in ("BACKTEST", "PAPER", "LIVE"):
        mode = ue.state.app_mode

    # Separate trade_direction from config fields
    direction = (data.pop("trade_direction", None) or "").upper()
    if direction not in ("CALL", "PUT", "BOTH"):
        # Preserve existing direction for this mode if not sent
        db = SessionLocal()
        try:
            user     = db.get(User, uid)
            existing = get_mode_settings(user.settings_json if user else "", mode)
            direction = existing.get("trade_direction", "BOTH")
        finally:
            db.close()

    # Build the mode dict: config fields + direction
    mode_dict = {**data, "trade_direction": direction}

    # Persist to DB
    db = SessionLocal()
    try:
        user = db.get(User, uid)
        if user:
            user.settings_json = set_mode_settings(
                user.settings_json or "", mode, mode_dict
            )
            db.commit()
            logger.info(f"Settings persisted ({mode}) for user {uid}")
    except Exception as e:
        logger.warning(f"Settings persist failed for user {uid}: {e}")
    finally:
        db.close()

    # Apply immediately if this is the currently running mode
    if mode == ue.state.app_mode:
        apply_config_dict(ue.config, data)
        ue.state.trade_direction = direction
        labels = {"CALL": "CALL only", "PUT": "PUT only", "BOTH": "CALL & PUT"}
        ue.state.logs.append(
            f"[--:--:--] ⚙ {mode} settings updated — direction: {labels[direction]}"
        )

    return jsonify({"ok": True, "mode": mode, "settings": config_to_dict(ue.config),
                    "trade_direction": direction})


# ── Mode switching ────────────────────────────────────────────────────────────

@dashboard_bp.route("/api/mode", methods=["POST"])
@jwt_required()
def switch_mode():
    uid      = _uid()
    ue       = engine_pool.get_or_create(uid)
    new_mode = (request.json or {}).get("mode", "").upper()
    if new_mode not in VALID_MODES:
        return jsonify({"error": f"Invalid mode. Must be one of {VALID_MODES}"}), 400
    if new_mode == ue.state.app_mode:
        return jsonify({"mode": new_mode, "changed": False})
    ue.switch_mode(new_mode)
    return jsonify({"mode": new_mode, "changed": True})


# ── Engine controls ───────────────────────────────────────────────────────────

@dashboard_bp.route("/api/trade-direction", methods=["POST"])
@jwt_required()
def set_trade_direction():
    """Set trade direction for the current mode and persist it.

    Body: {"direction": "CALL" | "PUT" | "BOTH"}
    """
    from config.config_utils import get_mode_settings, set_mode_settings
    from db.database import SessionLocal
    from db.models import User

    uid       = _uid()
    ue        = _ue()
    direction = ((request.json or {}).get("direction", "BOTH") or "BOTH").upper()
    if direction not in ("CALL", "PUT", "BOTH"):
        return jsonify({"ok": False, "error": "direction must be CALL, PUT, or BOTH"}), 400

    mode = ue.state.app_mode
    ue.state.trade_direction = direction

    # Persist direction in the current mode's settings
    db = SessionLocal()
    try:
        user = db.get(User, uid)
        if user:
            existing = get_mode_settings(user.settings_json or "", mode)
            existing["trade_direction"] = direction
            user.settings_json = set_mode_settings(
                user.settings_json or "", mode, existing
            )
            db.commit()
    except Exception as e:
        logger.warning(f"Direction persist failed for user {uid}: {e}")
    finally:
        db.close()

    labels = {"CALL": "CALL only ▲", "PUT": "PUT only 🔻", "BOTH": "CALL & PUT"}
    ue.state.logs.append(f"[--:--:--] 🎯 Trade direction set to {labels[direction]}")
    return jsonify({"ok": True, "trade_direction": direction})


@dashboard_bp.route("/api/trades-enabled", methods=["POST"])
@jwt_required()
def set_trades_enabled():
    """Toggle whether the engine enters new trades.

    Body: {"enabled": true|false}
    When disabled the engine still monitors the market and logs breakout signals,
    but skips BUY entries.  Any open position continues to be managed normally.
    """
    ue      = _ue()
    enabled = bool((request.json or {}).get("enabled", True))
    ue.state.trades_enabled = enabled
    verb = "enabled" if enabled else "disabled"
    icon = "▶" if enabled else "⏸"
    ue.state.logs.append(f"[--:--:--] {icon} Trade execution {verb} by user")
    return jsonify({"ok": True, "trades_enabled": enabled, "mode": ue.state.app_mode})


@dashboard_bp.route("/api/active-strategy", methods=["POST"])
@jwt_required()
def set_active_strategy():
    """Select which strategy is active for the running engine.

    Body: {"strategy_id": <int>}
    Activates the strategy in the DB (deactivates all others for this user)
    and records the selection on BotState so it survives across poll calls.
    """
    uid  = _uid()
    ue   = engine_pool.get_or_create(uid)
    data = request.json or {}
    sid  = data.get("strategy_id")

    if sid is None:
        return jsonify({"ok": False, "error": "strategy_id is required"}), 400

    try:
        sid = int(sid)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "strategy_id must be an integer"}), 400

    try:
        from db.database import SessionLocal
        from db.models import Strategy
        db = SessionLocal()
        try:
            s = db.get(Strategy, sid)
            if not s or s.user_id != uid:
                return jsonify({"ok": False, "error": "Strategy not found"}), 404
            # Deactivate all, activate selected
            db.query(Strategy).filter_by(user_id=uid).update({"is_active": False})
            s.is_active = True
            db.commit()
            ue.state.active_strategy_id = sid
            ue.state.logs.append(f"[--:--:--] 📋 Strategy changed: {s.name}")
            return jsonify({"ok": True, "active_strategy_id": sid, "name": s.name})
        finally:
            db.close()
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── Historical backtest ───────────────────────────────────────────────────────

@dashboard_bp.route("/api/backtest/run", methods=["POST"])
@jwt_required()
def run_historical_backtest():
    ue   = _ue()
    data = request.json or {}
    mode = data.get("mode", "single")

    def _check_auth(result: dict):
        err = result.get("error", "")
        if err:
            from execution.broker import is_kite_auth_error
            if is_kite_auth_error(Exception(err)):
                ue.state.kite_auth_error = True

    # Use IST for "today" and "market closed" so checks are timezone-correct
    # regardless of the server's local timezone (Render runs UTC).
    _IST         = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
    _now_ist     = datetime.datetime.now(tz=_IST)
    today        = _now_ist.date()
    # NSE market session ends at 15:30 IST.  After that, today's data is
    # complete and can be backtested just like any other historical day.
    market_closed = _now_ist.time() >= datetime.time(15, 30)

    if mode == "single":
        date_str = data.get("date", "")
        try:
            date = datetime.date.fromisoformat(date_str)
        except ValueError:
            return jsonify({"error": "Invalid date. Use YYYY-MM-DD format."}), 400
        if date > today:
            return jsonify({"error": "Backtest date cannot be in the future."}), 400
        if date == today and not market_closed:
            return jsonify({"error": "Today's session is not yet complete — use Paper or Live mode to trade today."}), 400
        result = ue.backtester.run_day(date)
        _check_auth(result)
        return jsonify(result)

    elif mode == "range":
        try:
            from_date = datetime.date.fromisoformat(data.get("from_date", ""))
            to_date   = datetime.date.fromisoformat(data.get("to_date",   ""))
        except ValueError:
            return jsonify({"error": "Invalid dates. Use YYYY-MM-DD format."}), 400
        if to_date > today:
            return jsonify({"error": "Backtest end date cannot be in the future."}), 400
        if to_date == today and not market_closed:
            return jsonify({"error": "Today's session is not yet complete — use Paper or Live mode to trade today. Set the end date to yesterday or earlier."}), 400
        if from_date > to_date:
            return jsonify({"error": "From date must be before To date."}), 400
        result = ue.backtester.run_range(from_date, to_date)
        _check_auth(result)
        return jsonify(result)

    return jsonify({"error": "mode must be 'single' or 'range'"}), 400


# ── Option chart ──────────────────────────────────────────────────────────────

@dashboard_bp.route("/api/option-chart")
@jwt_required()
def option_chart():
    ue       = _ue()
    strike   = request.args.get("strike", type=int)
    opt_type = (request.args.get("type", "CE") or "CE").upper()
    date_str = request.args.get("date", "")
    interval = request.args.get("interval", "minute")

    if not strike:
        return jsonify({"ok": False, "error": "strike is required"}), 400
    if opt_type not in ("CE", "PE"):
        return jsonify({"ok": False, "error": "type must be CE or PE"}), 400
    try:
        trade_date = (datetime.date.fromisoformat(date_str)
                      if date_str else datetime.date.today())
    except ValueError:
        return jsonify({"ok": False, "error": "Invalid date format"}), 400

    try:
        records, contract = ue.broker.get_option_history(strike, opt_type, trade_date, interval)
        if contract is None:
            return jsonify({
                "ok":    False,
                "error": f"No NFO contract found for NIFTY {strike}{opt_type} "
                         f"expiring on/after {trade_date}",
            }), 404
        candles = [
            {
                "time":  int(r["date"].timestamp()),
                "open":  r["open"], "high": r["high"],
                "low":   r["low"],  "close": r["close"],
            }
            for r in records
        ]
        return jsonify({
            "ok":            True,
            "data":          candles,
            "label":         f"NIFTY {strike} {opt_type}",
            # Exact contract resolved — lets the UI display the actual expiry
            # so users can verify against Kite (weekly vs monthly mismatch check)
            "tradingsymbol": contract["tradingsymbol"],
            "expiry":        contract["expiry"],
            # Include the instrument token so the frontend can subscribe to
            # live WebSocket ticks for this contract (ticker overlay).
            "token":         contract["token"],
        })
    except Exception as e:
        from execution.broker import is_kite_auth_error
        if is_kite_auth_error(e):
            ue.state.kite_auth_error = True
        return jsonify({"ok": False, "error": str(e)}), 500


# ── NIFTY history chart ───────────────────────────────────────────────────────

def _to_date(dt) -> datetime.date:
    return dt.date() if hasattr(dt, "date") else dt


def _day_key(d: datetime.date) -> dict:
    return {"year": d.year, "month": d.month, "day": d.day}


def _aggregate_candles(records: list, interval: str) -> list:
    buckets: dict = {}
    for r in records:
        d   = _to_date(r["date"])
        key = (d - datetime.timedelta(days=d.weekday())
               if interval == "week"
               else datetime.date(d.year, d.month, 1))
        if key not in buckets:
            buckets[key] = {"time":  _day_key(key),
                            "open":  r["open"], "high": r["high"],
                            "low":   r["low"],  "close": r["close"]}
        else:
            b = buckets[key]
            b["high"]  = max(b["high"],  r["high"])
            b["low"]   = min(b["low"],   r["low"])
            b["close"] = r["close"]
    return [buckets[k] for k in sorted(buckets)]


@dashboard_bp.route("/api/nifty/history")
@jwt_required()
def nifty_history():
    ue       = _ue()
    interval = (request.args.get("interval", "day") or "day").lower()
    if interval not in ("day", "week", "month"):
        interval = "day"

    today        = datetime.date.today()
    default_days = {"day": 365, "week": 730, "month": 1825}
    default_from = today - datetime.timedelta(days=default_days.get(interval, 365))

    try:
        from_dt = datetime.date.fromisoformat(
            request.args.get("from", str(default_from)))
        to_dt   = datetime.date.fromisoformat(
            request.args.get("to",   str(today)))
    except ValueError:
        return jsonify({"ok": False, "error": "Invalid date format"}), 400

    try:
        records = ue.broker.get_historical_data(
            ue.config.index_token,
            f"{from_dt} 09:15:00",
            f"{to_dt} 15:30:00",
            "day",
            state=ue.state,
        )
        if interval == "day":
            candles = [
                {"time":  _day_key(_to_date(r["date"])),
                 "open":  r["open"], "high": r["high"],
                 "low":   r["low"],  "close": r["close"]}
                for r in records
            ]
        else:
            candles = _aggregate_candles(records, interval)
        return jsonify({"ok": True, "data": candles, "interval": interval})
    except Exception as e:
        from execution.broker import is_kite_auth_error
        if is_kite_auth_error(e):
            ue.state.kite_auth_error = True
        return jsonify({"ok": False, "error": str(e)}), 500


# ── Option Chain (Kite Connect) ───────────────────────────────────────────────
# Uses the existing Kite session — reliable, no external scraping needed.
# NSE blocks server-side requests with Akamai, so Kite is the only stable source.

import time as _time_mod

_kite_oc_cache: dict = {"data": None, "ts": 0.0, "expiry": ""}


def _fmt_exp(d: datetime.date) -> str:
    """Format a date as 'DD-Mon-YYYY' (e.g. '22-May-2026') matching NSE/Kite style."""
    return d.strftime("%d-%b-%Y")


def _parse_exp(s: str) -> datetime.date | None:
    """Parse 'DD-Mon-YYYY' or ISO 'YYYY-MM-DD' back to a date. Returns None on failure."""
    for fmt in ("%d-%b-%Y", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _as_date_oc(v) -> datetime.date | None:
    if isinstance(v, datetime.datetime):
        return v.date()
    if isinstance(v, datetime.date):
        return v
    try:
        return datetime.date.fromisoformat(str(v)[:10])
    except Exception:
        return None


@dashboard_bp.route("/api/option-chain-nse")
@jwt_required()
def option_chain_nse():
    """
    Return NIFTY option chain via Kite Connect (LTP + OI for ATM ± 15 strikes).
    Results are server-cached for 30 seconds.

    Query params:
      expiry  — 'DD-Mon-YYYY'  (default: nearest upcoming expiry)
    """
    ue         = _ue()
    expiry_str = (request.args.get("expiry", "") or "").strip()

    # ── Serve from 30-second cache ────────────────────────────────────────────
    now = _time_mod.time()
    cached = _kite_oc_cache["data"]
    if cached and (now - _kite_oc_cache["ts"]) < 30 and _kite_oc_cache["expiry"] == expiry_str:
        return jsonify({"ok": True, "cached": True, **cached})

    try:
        # 1 — Spot price
        spot = ue.broker.get_ltp("NSE:NIFTY 50")

        # 2 — NFO instruments (cached by broker, refreshed once per day)
        insts = ue.broker.get_nfo_instruments()

        # 3 — Filter NIFTY options
        today      = datetime.date.today()
        nifty_opts = [
            i for i in insts
            if i.get("name") == "NIFTY"
            and i.get("instrument_type") in ("CE", "PE")
        ]

        # 4 — Build sorted list of upcoming expiries
        exp_dates = sorted(
            {
                d
                for i in nifty_opts
                if (d := _as_date_oc(i.get("expiry"))) and d >= today
            }
        )
        expiry_labels = [_fmt_exp(d) for d in exp_dates]

        # 5 — Select target expiry
        target_date: datetime.date | None = None
        if expiry_str:
            target_date = _parse_exp(expiry_str)
        if target_date is None or target_date not in exp_dates:
            target_date = exp_dates[0] if exp_dates else None

        if target_date is None:
            return jsonify({"ok": False, "error": "No upcoming NIFTY expiry found"}), 404

        target_label = _fmt_exp(target_date)

        # 6 — ATM and ± 15 strikes (step = 50)
        step    = 50
        atm_raw = round(spot / step) * step
        strikes = {atm_raw + i * step for i in range(-15, 16)}

        # 7 — Instruments for target expiry + relevant strikes
        target_insts = [
            i for i in nifty_opts
            if _as_date_oc(i.get("expiry")) == target_date
            and int(float(i.get("strike", 0))) in strikes
        ]

        if not target_insts:
            return jsonify({
                "ok":       True,
                "spot":     spot,
                "atm":      atm_raw,
                "expiry":   target_label,
                "expiries": expiry_labels,
                "data":     [],
                "error":    f"No contracts found for {target_label}",
            })

        # 8 — Batch quote (Kite allows up to 500 symbols per call)
        symbols = [f"NFO:{i['tradingsymbol']}" for i in target_insts]
        quotes  = ue.broker.kite.quote(symbols)

        # 9 — Build chain dict  {strike: {ce: {...}, pe: {...}}}
        chain: dict[int, dict] = {}
        for inst in target_insts:
            sym       = f"NFO:{inst['tradingsymbol']}"
            q         = quotes.get(sym, {})
            sk        = int(float(inst["strike"]))
            side      = inst["instrument_type"].lower()   # "ce" or "pe"
            if sk not in chain:
                chain[sk] = {"strike": sk, "ce": {}, "pe": {}}

            ltp        = float(q.get("last_price", 0) or 0)
            oi         = int(  q.get("oi",         0) or 0)
            volume     = int(  q.get("volume",      0) or 0)
            prev_close = float((q.get("ohlc") or {}).get("close", 0) or 0)
            chg_pct    = round((ltp - prev_close) / prev_close * 100, 2) if prev_close else 0.0
            chain[sk][side] = {
                "ltp":     round(ltp, 2),
                "oi":      oi,
                "oi_chg":  0,
                "volume":  volume,
                "iv":      0.0,
                "chg_pct": chg_pct,
            }

        sorted_rows = sorted(chain.values(), key=lambda x: x["strike"])
        atm_nearest = min(chain.keys(), key=lambda k: abs(k - spot)) if chain else atm_raw

        result = {
            "spot":     round(spot, 2),
            "atm":      atm_nearest,
            "expiry":   target_label,
            "expiries": expiry_labels,
            "data":     sorted_rows,
        }

        # Cache the result
        _kite_oc_cache["data"]   = result
        _kite_oc_cache["ts"]     = _time_mod.time()
        _kite_oc_cache["expiry"] = expiry_str

        return jsonify({"ok": True, **result})

    except Exception as e:
        from execution.broker import is_kite_auth_error
        if is_kite_auth_error(e):
            ue.state.kite_auth_error = True
        logger.warning(f"Kite option-chain failed: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500
