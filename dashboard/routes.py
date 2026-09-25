from execution.order_safety import has_exposure
from config.settings import TradingConfig
import datetime
import logging
import threading
import time

from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required

from config.config_utils import config_to_dict, apply_config_dict
from core.engine_pool import engine_pool
from dashboard.authz import admin_required, personal_execution_required

logger = logging.getLogger(__name__)

dashboard_bp = Blueprint("dashboard", __name__)

VALID_MODES = {"BACKTEST", "PAPER", "LIVE"}


@dashboard_bp.after_request
def compress_chart_history(response):
    if request.endpoint != 'dashboard.chart_history' or response.status_code != 200:
        return response
    response.vary.add('Accept-Encoding')
    if request.accept_encodings['gzip'] > 0 and not response.headers.get('Content-Encoding'):
        import gzip
        body = response.get_data()
        if len(body) > 2048:
            response.set_data(gzip.compress(body, compresslevel=3))
            response.headers['Content-Encoding'] = 'gzip'
    return response


# ── Internal helpers ──────────────────────────────────────────────────────────

def _uid() -> int:
    return int(get_jwt_identity())


def _ue():
    """Return the calling user's UserEngine (creates one if needed)."""
    return engine_pool.get_or_create(_uid())


def _chart_records(token, symbol, start, end, interval, fetch, *, bypass=None, cached_only=False):
    from execution.history_cache import HistoryCache
    cache = HistoryCache()
    records, hit = cache.load((_uid(), symbol, token, interval, str(start), str(end)),
                               end if isinstance(end, datetime.date) else datetime.date.fromisoformat(str(end)[:10]),
                               fetch, bypass=request.args.get('refresh') == '1' if bypass is None else bypass,
                               cached_only=cached_only)
    return records, hit, cache.fetched_at



# ── Pages ─────────────────────────────────────────────────────────────────────

@dashboard_bp.route("/")
def index():
    # mode is no longer needed server-side — JS fetches /api/state after load
    from dashboard.frontend import page
    return page()


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
    from db.database import SessionLocal
    from db.models import User as UserModel
    uid = _uid()
    ue  = _ue()
    d   = ue.state.to_dict()
    # Expose the configured trade quantity so the UI can display it directly
    # rather than trying to back-calculate it from live_pnl (which fluctuates).
    d["qty"] = ue.config.qty
    d["engine_running"] = ue.is_running
    d["in_position"] = bool(ue._engine and ue._engine.strategy.in_position)
    d["execution_blocked"] = bool(ue._engine and ue._engine.execution_blocked)
    d["equity_engines"] = ue.equity_snapshots()
    # Include DB-persisted background_trading flag so the UI button stays in sync
    # after a page refresh or server restart.
    try:
        db = SessionLocal()
        try:
            user = db.get(UserModel, uid)
            d["background_trading"] = bool(
                user.background_trading
                if user and user.background_trading is not None
                else True
            )
        finally:
            db.close()
    except Exception:
        d["background_trading"] = True
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
    result = {**config_to_dict(TradingConfig()), **mode_data}
    result["trade_direction"] = mode_data.get(
        "trade_direction",
        "BOTH",
    )
    return jsonify(result)


@dashboard_bp.route("/api/settings", methods=["POST"])
@jwt_required()
@admin_required
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
        return jsonify(ok=False, error="Invalid trading mode."), 400

    from dashboard.api_support import validate_settings
    with SessionLocal() as session:
        saved_user = session.get(User, uid)
        saved = get_mode_settings(saved_user.settings_json or '', mode)
    validate_settings({**config_to_dict(TradingConfig()), **saved, **data})
    data = {**saved, **data}

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
    except Exception:
        db.rollback()
        logger.exception("Settings persistence failed")
        return jsonify(ok=False, error="Settings were not saved."), 500
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
@personal_execution_required
def switch_mode():
    uid      = _uid()
    ue       = engine_pool.get_or_create(uid)
    new_mode = (request.json or {}).get("mode", "").upper()
    if new_mode not in VALID_MODES:
        return jsonify({"error": f"Invalid mode. Must be one of {VALID_MODES}"}), 400
    if new_mode == ue.state.app_mode:
        return jsonify({"mode": new_mode, "changed": False})
    if has_exposure(ue):
        return jsonify(ok=False, error='Close the open position before changing mode.'), 409
    try:
        ue.switch_mode(new_mode)
    except RuntimeError as exc:
        return jsonify(ok=False, error=str(exc)), 409
    return jsonify({"mode": new_mode, "changed": True})


# ── Engine controls ───────────────────────────────────────────────────────────

@dashboard_bp.route("/api/trade-direction", methods=["POST"])
@jwt_required()
@admin_required
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


@dashboard_bp.route("/api/manual-trade", methods=["POST"])
@jwt_required()
@admin_required
def manual_trade():
    """Manually enter or exit the ORB options position.
    Body: {"action": "enter"|"exit", "direction": "CALL"|"PUT"}"""
    ue   = _ue()
    data = request.json or {}
    action = (data.get("action") or "").lower()
    if ue.state.app_mode not in ("PAPER", "LIVE"):
        return jsonify({"ok": False, "error": "Switch to Paper or Live first."}), 400
    if not ue.is_running:
        return jsonify({"ok": False, "error": "Engine not running — press Run first."}), 400
    if action == "exit":
        ue.state.manual_action = "EXIT"
        return jsonify({"ok": True, "queued": "EXIT"})
    if action == "enter":
        direction = (data.get("direction") or "CALL").upper()
        if direction not in ("CALL", "PUT"):
            return jsonify({"ok": False, "error": "direction must be CALL or PUT"}), 400
        ue.state.manual_action = "ENTER_" + direction
        return jsonify({"ok": True, "queued": "ENTER_" + direction})
    return jsonify({"ok": False, "error": "action must be enter or exit"}), 400


@dashboard_bp.route("/api/trades-enabled", methods=["POST"])
@jwt_required()
@personal_execution_required
def set_trades_enabled():
    """Toggle whether the engine enters new trades.

    Body: {"enabled": true|false}
    When disabled the engine still monitors the market and logs breakout signals,
    but skips BUY entries.  Any open position continues to be managed normally.
    """
    ue      = _ue()
    enabled = bool((request.json or {}).get("enabled", True))
    if enabled and (ue.state.kite_auth_error or not ue.broker.kite.access_token):
        return jsonify(ok=False, error='Connect Kite before enabling trading.'), 409
    if enabled and ue.state.app_mode == 'BACKTEST':
        return jsonify(ok=False, error='Use Backtest Lab to run historical sessions.'), 400
    previous = ue.state.trades_enabled
    ue.state.trades_enabled = enabled
    verb = "enabled" if enabled else "disabled"
    icon = "▶" if enabled else "⏸"
    ue.state.logs.append(f"[--:--:--] {icon} Trade execution {verb} by user")
    # Enabling trades with no engine running would silently do nothing (the
    # flag only gates an engine loop that isn't there) — start it.
    if enabled and not ue.is_running:
        mode = ue.state.app_mode or "PAPER"
        try:
            ue.start(mode)
        except Exception:
            ue.state.trades_enabled = previous
            logger.exception('Engine start blocked')
            return jsonify(ok=False, error='Engine start blocked. Verify Kite positions, pending orders and session; resolve any exposure before retrying.'), 409
        logger.info(f"Take Trades enabled — started {mode} engine")
        ue.state.logs.append(f"[--:--:--] ▶ Engine started in {mode} mode")
    return jsonify({"ok": True, "trades_enabled": enabled, "mode": ue.state.app_mode})


@dashboard_bp.route("/api/background-trading", methods=["POST"])
@jwt_required()
@admin_required
def set_background_trading():
    """Toggle persistent background auto-trading.

    Body: {"enabled": true|false}

    When enabled:
      • Engine auto-starts on server restart (if today's Kite token exists in DB).
      • Trades continue to execute even when no browser is open.
      • The flag is persisted in DB so it survives restarts.

    When disabled:
      • Engine will NOT auto-start on the next server restart.
      • Current session's engine is paused (trades_enabled → False) so no new
        positions are opened.  Any open position is still managed to exit.
      • User must re-enable from the dashboard to resume auto-trading.
    """
    from db.database import SessionLocal
    from db.models import User as UserModel

    uid     = _uid()
    ue      = _ue()
    enabled = bool((request.json or {}).get("enabled", True))

    if enabled and (ue.state.kite_auth_error or not ue.broker.kite.access_token):
        return jsonify(ok=False, error='Connect Kite before enabling trading.'), 409

    # Persist to DB so the flag survives server restarts
    try:
        db = SessionLocal()
        try:
            user = db.get(UserModel, uid)
            if user:
                user.background_trading = enabled
                db.commit()
        finally:
            db.close()
    except Exception:
        logger.exception("Background setting persistence failed")
        return jsonify(ok=False, error="Background preference was not saved."), 500

    if enabled:
        # Opting into restart behavior must not submit orders or resume live entries.
        ue.state.logs.append("[--:--:--] Paper auto-resume enabled for the next server restart")
    else:
        # Pause trade execution (engine keeps running to manage any open position)
        ue.state.trades_enabled = False
        logger.info(f"Background trading disabled for user {uid} — trades paused")
        ue.state.logs.append("[--:--:--] ⛔ Background auto-trading DISABLED — engine will not auto-start on restart")

    return jsonify({"ok": True, "background_trading": enabled, "trades_enabled": ue.state.trades_enabled})


def _apply_options_strategy(ue, rules: dict):
    """Map a Builder options strategy's flat params onto the ORB TradingConfig.
    Only keys present are applied; missing keys keep the current config."""
    import datetime as _dt
    if not isinstance(rules, dict):
        return
    cfg = ue.config
    try:
        if "target_pts" in rules:  cfg.target_pts = int(rules["target_pts"])
        if "fib_trail" in rules:   cfg.fib_trail = float(rules["fib_trail"])
        if "strike_spacing" in rules: cfg.strike_spacing = int(rules["strike_spacing"])
        if "max_daily_loss" in rules: cfg.max_daily_loss = float(rules["max_daily_loss"])
        if "lot_size" in rules: cfg.lot_size = int(rules["lot_size"])
        if "lots" in rules:        cfg.qty_multiplier = int(rules["lots"])
        for k, attr in (("or_end_time","or_end_time"),("entry_end_time","entry_end_time"),
                        ("eod_exit_time","eod_exit_time")):
            if k in rules and rules[k]:
                hh, mm = str(rules[k]).split(":")[:2]
                setattr(cfg, attr, _dt.time(int(hh), int(mm)))
        if "direction" in rules and rules["direction"]:
            ue.state.trade_direction = str(rules["direction"]).upper()
    except Exception as e:
        logger.warning(f"apply options strategy params failed: {e}")


@dashboard_bp.route("/api/active-strategy", methods=["POST"])
@jwt_required()
@admin_required
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
            # Apply an options strategy's tunable params to the live ORB config
            # so different options strategies actually trade differently.
            if (s.instrument_type or "OPTIONS") == "OPTIONS":
                _apply_options_strategy(ue, s.get_rules())
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
    if ue.state.kite_auth_error or not ue.broker.kite.access_token:
        return jsonify(ok=False, error='Connect Kite in Profile to load historical data for backtesting.'), 409
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

    direction = (data.get("direction", "BOTH") or "BOTH").upper()
    if direction not in ("CALL", "PUT", "BOTH"):
        direction = "BOTH"

    # Optional per-request config overrides (used by analytics compare feature)
    # These are applied temporarily to a cloned config — the live engine config
    # is never mutated.
    bt_cfg = _backtest_config()
    if "or_end_time" in data:
        try:
            parts = str(data["or_end_time"]).split(":")
            bt_cfg.or_end_time = datetime.time(int(parts[0]), int(parts[1]))
        except Exception:
            pass
    if "target_pts" in data:
        try:
            bt_cfg.target_pts = int(data["target_pts"])
        except Exception:
            pass

    # Use the (possibly overridden) config for this request's backtester
    from execution.historical_backtest import HistoricalBacktester
    backtester = HistoricalBacktester(bt_cfg, ue.backtester.broker)

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
        result = backtester.run_day(date, direction=direction)
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
        result = backtester.run_range(from_date, to_date, direction=direction)
        _check_auth(result)
        return jsonify(result)

    return jsonify({"error": "mode must be 'single' or 'range'"}), 400


# ── Strategy optimizer (grid search) ─────────────────────────────────────────

@dashboard_bp.route("/api/backtest/optimize", methods=["POST"])
@jwt_required()
def run_backtest_optimize():
    ue   = _ue()
    data = request.json or {}

    from_date_str = data.get("from_date", "")
    to_date_str   = data.get("to_date",   "")
    try:
        from_date = datetime.date.fromisoformat(from_date_str)
        to_date   = datetime.date.fromisoformat(to_date_str)
    except ValueError:
        return jsonify({"error": "Invalid dates. Use YYYY-MM-DD format."}), 400

    _IST      = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
    _now_ist  = datetime.datetime.now(tz=_IST)
    today     = _now_ist.date()
    if to_date > today:
        return jsonify(ok=False, error="End date cannot be in the future."), 400

    # ── Parameter ranges sent from the frontend ───────────────────────────────
    or_times   = data.get("or_times",   ["09:20","09:25","09:30","09:35","09:40","09:45"])
    targets    = [int(t) for t in data.get("targets",   [80, 100, 120, 140, 160, 180, 200])]
    directions = [d.upper() for d in data.get("directions", ["CALL", "BOTH", "PUT"])
                  if d.upper() in ("CALL", "PUT", "BOTH")]
    metric     = data.get("metric", "total_pnl")
    if metric not in ("total_pnl", "win_rate", "profit_factor", "trade_days"):
        metric = "total_pnl"

    if not or_times or not targets or not directions:
        return jsonify({"error": "No parameter combinations to test."}), 400

    from execution.historical_backtest import HistoricalBacktester

    bt_cfg    = _backtest_config()
    backtester = HistoricalBacktester(bt_cfg, ue.backtester.broker)

    result = backtester.optimize(
        from_date, to_date,
        or_times=or_times,
        targets=targets,
        directions=directions,
        metric=metric,
    )
    return jsonify(result)


# ── Option chart ──────────────────────────────────────────────────────────────

@dashboard_bp.route('/api/option-contracts')
@jwt_required()
def option_contracts():
    ue = _ue()
    try:
        today = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=5, minutes=30))).date()
        contracts = [dict(symbol=i['tradingsymbol'], expiry=str(i['expiry']),
                          strike=i['strike'], type=i['instrument_type'])
                     for i in ue.broker.get_nfo_instruments()
                     if i.get('name') == 'NIFTY' and i.get('instrument_type') in ('CE', 'PE')
                     and str(i.get('expiry', '')) >= str(today)]
        return jsonify(ok=True, contracts=sorted(contracts, key=lambda c: (c['expiry'], c['strike'], c['type'])))
    except Exception:
        return jsonify(ok=False, error='Could not load option contracts from Kite. Check your connection and retry.'), 502

@dashboard_bp.route("/api/option-chart")
@jwt_required()
def option_chart():
    ue       = _ue()
    symbol = request.args.get('symbol', '').strip()
    if symbol:
        try:
            contract = next((i for i in ue.broker.get_nfo_instruments()
                             if i.get('tradingsymbol') == symbol and i.get('name') == 'NIFTY'
                             and i.get('instrument_type') in ('CE', 'PE')), None)
            if not contract:
                return jsonify(ok=False, error='Option contract not found. Refresh the contract list.'), 404
            now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=5, minutes=30)))
            start = (now - datetime.timedelta(days=7)).strftime('%Y-%m-%d 09:15:00')
            end = now.strftime('%Y-%m-%d 15:30:00')
            records, cache_hit, history_as_of = _chart_records(contract['instrument_token'], symbol, start, end, '5minute',
                lambda: ue.broker.get_historical_data(contract['instrument_token'], start, end, '5minute'))
            candles = [dict(time=int(r['date'].timestamp()), open=r['open'], high=r['high'],
                            low=r['low'], close=r['close']) for r in records]
            return jsonify(ok=True, data=candles, tradingsymbol=symbol, expiry=str(contract['expiry']), token=contract['instrument_token'], cache_hit=cache_hit, history_as_of=history_as_of)
        except Exception:
            return jsonify(ok=False, error='Could not load option history from Kite. Check your connection and retry.'), 502
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

    def _to_candles(records):
        return [
            {
                "time":  int(r["date"].timestamp()),
                "open":  r["open"], "high": r["high"],
                "low":   r["low"],  "close": r["close"],
            }
            for r in records
        ]

    try:
        # Use get_expiry_date() so we consistently resolve the same contract
        # as the live order (e.g. June 2 weekly) rather than the nearest
        # calendar expiry (e.g. May 26 monthly) which has different prices.
        from core.options_math import OptionsMath
        min_expiry = OptionsMath.get_expiry_date(trade_date)

        records, contract = ue.broker.get_option_history(
            strike, opt_type, trade_date, interval, min_expiry=min_expiry
        )
        if contract is None:
            return jsonify({
                "ok":    False,
                "error": f"No NFO contract found for NIFTY {strike}{opt_type} "
                         f"expiring on/after {min_expiry}",
            }), 404

        candles = _to_candles(records)

        # Also fetch the previous trading day for the SAME contract (min_expiry
        # is passed so we don't accidentally switch to a different expiry when
        # fetching data for an earlier date).
        # Skip weekends and NSE holidays so we never waste an API call on a day
        # that has no data and immediately try the day before.
        prev_candles: list = []
        for delta in range(1, 10):   # wider range: skipped days don't count as attempts
            prev_date = trade_date - datetime.timedelta(days=delta)
            if not OptionsMath.is_trading_day(prev_date):
                continue
            try:
                prev_records, _ = ue.broker.get_option_history(
                    strike, opt_type, prev_date, interval, min_expiry=min_expiry
                )
                if prev_records:
                    prev_candles = _to_candles(prev_records)
                    break
            except Exception:
                pass   # holiday / no data — try the day before

        return jsonify({
            "ok":            True,
            "data":          prev_candles + candles,
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


# ── Recalculate a saved trade's entry price from real Kite data ───────────────

@dashboard_bp.route("/api/trade/<int:trade_id>/recalculate-entry", methods=["POST"])
@jwt_required()
def recalculate_trade_entry(trade_id):
    """
    Re-fetch the real 1-min option LTP at entry_time from Kite historical data,
    then update entry_prem + recalculate gross_pnl / charges / net_pnl in the DB.

    Use this to correct a trade that was saved with a Black-Scholes entry price
    (e.g. when get_option_ltp failed at the moment of the breakout).
    Also patches the live in-memory state so the dashboard updates immediately
    without requiring an engine restart.
    """
    from db.database import SessionLocal
    from db.models import Trade as TradeModel
    from core.options_math import OptionsMath

    _IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
    uid  = _uid()
    ue   = _ue()
    cfg  = ue.config

    db = SessionLocal()
    try:
        trade = db.get(TradeModel, trade_id)
        if not trade or trade.user_id != uid:
            return jsonify({"ok": False, "error": "Trade not found"}), 404

        if not trade.entry_time or not trade.strike or not trade.position_type:
            return jsonify({"ok": False, "error":
                            "Trade missing entry_time, strike, or position_type"}), 400

        opt_type   = "CE" if trade.position_type == "CALL" else "PE"
        trade_date = trade.date   # datetime.date

        # ── Fetch real 1-min option history ───────────────────────────────────
        min_expiry = OptionsMath.get_expiry_date(trade_date)
        records, contract_info = ue.broker.get_option_history(
            trade.strike, opt_type, trade_date,
            interval="minute", min_expiry=min_expiry,
        )
        if not records:
            return jsonify({"ok": False, "error":
                f"No 1-min data from Kite for NIFTY{trade.strike}{opt_type} "
                f"on {trade_date} (expiry {min_expiry})"}), 404

        # Build timestamp → close map (round to minute boundary)
        opt_price_map: dict = {}
        for r in records:
            r_dt = r["date"]
            if r_dt.tzinfo is None:
                r_dt = r_dt.replace(tzinfo=_IST)
            opt_price_map[(int(r_dt.timestamp()) // 60) * 60] = r["close"]

        # Entry time is stored as naive IST in the DB
        entry_aware = trade.entry_time.replace(tzinfo=_IST)
        entry_ts    = (int(entry_aware.timestamp()) // 60) * 60

        # Exact match, then ±1 minute tolerance for any second-level offset
        real_entry = (opt_price_map.get(entry_ts)
                      or opt_price_map.get(entry_ts - 60)
                      or opt_price_map.get(entry_ts + 60))

        if real_entry is None:
            closest = min(opt_price_map, key=lambda k: abs(k - entry_ts), default=None)
            return jsonify({"ok": False,
                "error": f"No option candle within ±1 min of entry {trade.entry_time}",
                "closest_ts": closest,
                "closest_price": opt_price_map.get(closest),
            }), 404

        # ── Recalculate P&L with the correct entry ────────────────────────────
        qty       = trade.quantity or cfg.qty
        exit_prem = float(trade.exit_prem or 0)
        new_entry = round(float(real_entry), 2)

        gross_pnl = round((exit_prem - new_entry) * qty, 2)
        buy_val   = new_entry * qty
        sell_val  = exit_prem * qty
        turnover  = buy_val + sell_val
        brokerage = cfg.brokerage_per_order * 2
        stt       = sell_val * cfg.stt_pct
        exch      = turnover * cfg.exchange_charges_pct
        gst       = (brokerage + exch) * cfg.gst_pct
        sebi      = turnover * cfg.sebi_charges_pct
        stamp     = buy_val  * cfg.stamp_duty_pct
        charges   = round(brokerage + stt + exch + gst + sebi + stamp, 2)
        net_pnl   = round(gross_pnl - charges, 2)

        old_entry = trade.entry_prem
        old_net   = trade.net_pnl

        # ── Persist to DB ─────────────────────────────────────────────────────
        trade.entry_prem = new_entry
        trade.gross_pnl  = gross_pnl
        trade.charges    = charges
        trade.net_pnl    = net_pnl
        db.commit()

        # ── Patch live in-memory state so dashboard updates without restart ───
        st = ue.state
        if st.position_type == trade.position_type:
            st.entry_prem    = new_entry
            st.gross_pnl     = gross_pnl
            st.total_charges = charges
            st.net_pnl       = net_pnl
            st.pnl           = net_pnl
            st.target_prem   = round(new_entry + cfg.target_pts, 2)
            # Fix the option chart BUY marker label
            for m in st.option_markers:
                if "BUY" in m.get("text", ""):
                    m["text"] = f"BUY {trade.position_type} @ ₹{new_entry:.0f}"
            pnl_sign = "+" if net_pnl >= 0 else ""
            st.status = (f"Trade done for today | "
                         f"{trade.position_type} {trade.strike} | "
                         f"P&L {pnl_sign}₹{net_pnl:.0f}")
            st.logs.append(
                f"[--:--:--] ✏ Entry recalculated: BS ₹{old_entry:.0f} → "
                f"real ₹{new_entry:.0f} | Net P&L ₹{old_net:.0f} → {pnl_sign}₹{net_pnl:.0f}"
            )

        logger.info(
            f"Trade {trade_id}: entry ₹{old_entry} → ₹{new_entry} | "
            f"net_pnl ₹{old_net:.2f} → ₹{net_pnl:.2f} "
            f"({contract_info['tradingsymbol'] if contract_info else 'n/a'})"
        )
        return jsonify({
            "ok":              True,
            "trade_id":        trade_id,
            "tradingsymbol":   contract_info["tradingsymbol"] if contract_info else None,
            "old_entry_prem":  old_entry,
            "new_entry_prem":  new_entry,
            "exit_prem":       exit_prem,
            "gross_pnl":       gross_pnl,
            "charges":         charges,
            "old_net_pnl":     old_net,
            "new_net_pnl":     net_pnl,
        })

    except Exception as e:
        db.rollback()
        from execution.broker import is_kite_auth_error
        if is_kite_auth_error(e):
            ue.state.kite_auth_error = True
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        db.close()


# ── NIFTY history chart ───────────────────────────────────────────────────────

def _to_date(dt) -> datetime.date:
    if isinstance(dt, str):
        dt = datetime.datetime.fromisoformat(dt)
    if isinstance(dt, datetime.datetime) and dt.tzinfo:
        dt = dt.astimezone(datetime.timezone(datetime.timedelta(hours=5, minutes=30)))
    return dt.date() if hasattr(dt, "date") else dt


def _day_key(d: datetime.date) -> dict:
    return {"year": d.year, "month": d.month, "day": d.day}


def _aggregate_candles(records: list, interval: str) -> list:
    buckets: dict = {}
    for r in sorted(records, key=lambda r: str(r["date"])):
        d   = _to_date(r["date"])
        key = (d - datetime.timedelta(days=d.weekday())
               if interval == "week"
               else datetime.date(d.year, d.month, 1))
        if key not in buckets:
            buckets[key] = {"time":  _day_key(key),
                            "open":  r["open"], "high": r["high"],
                            "low":   r["low"],  "close": r["close"], "volume": r.get("volume", 0)}
        else:
            b = buckets[key]
            b["high"]  = max(b["high"],  r["high"])
            b["low"]   = min(b["low"],   r["low"])
            b["close"] = r["close"]
            b["volume"] += r.get("volume", 0)
    return [buckets[k] for k in sorted(buckets)]


# Pace paginated chart downloads across panes to respect historical API limits.
_history_page_lock = threading.Lock()
_history_page_last = 0.0


def _history_page(broker, token, start, end, interval, state):
    global _history_page_last
    with _history_page_lock:
        time.sleep(max(0, 0.36 - (time.monotonic() - _history_page_last)))
        try:
            return broker.get_historical_data(token, f"{start} 09:15:00", f"{end} 15:30:00", interval, state=state)
        finally:
            _history_page_last = time.monotonic()


_nse_inst_cache: dict = {"data": [], "date": None}

_INDICES = [
    {"symbol": "NIFTY 50",    "name": "Nifty 50 Index",        "exchange": "NSE"},
    {"symbol": "BANKNIFTY",   "name": "Nifty Bank Index",       "exchange": "NSE"},
    {"symbol": "SENSEX",      "name": "BSE Sensex",             "exchange": "BSE"},
    {"symbol": "MIDCAPNIFTY", "name": "Nifty Midcap Select",    "exchange": "NSE"},
    {"symbol": "FINNIFTY",    "name": "Nifty Financial Services","exchange": "NSE"},
]

@dashboard_bp.route("/api/symbols/search")
@jwt_required()
def symbols_search():
    """Search NSE equity symbols + indices. Returns [{symbol, name, exchange}]."""
    from dashboard.instrument_catalog import catalog
    q=request.args.get('q','').strip().upper()[:100]
    raw_limit=request.args.get('limit','20')
    if not raw_limit.isdigit() or not 1<=int(raw_limit)<=100:
        return jsonify(ok=False,error='limit must be between 1 and 100.'),400
    records,stale=catalog(_ue().broker)
    equities_only=request.args.get('equity')=='1'
    candidates=records if equities_only else _INDICES+records
    matches={r['symbol']:r for r in candidates if not q or q in r['symbol'].upper() or q in r['name'].upper()}
    results=sorted(matches.values(),key=lambda r:(not r['symbol'].upper().startswith(q),r['symbol']))[:int(raw_limit)]
    return jsonify(ok=True,results=results,stale=stale,warning='Broker catalogue unavailable; showing saved instruments.' if stale else '')


@dashboard_bp.route("/api/chart/history")
@jwt_required()
def chart_history():
    """Batch adjacent cached pages without multiplying cold broker requests."""
    args = request.args.copy()
    try:
        batch = int(args.get('batch', '1'))
    except ValueError:
        return jsonify(ok=False, error='Invalid history batch size.'), 400
    if not 1 <= batch <= 128:
        return jsonify(ok=False, error='History batch must be between 1 and 128.'), 400
    response = _chart_history_page(args)
    if batch == 1 or args.get('range') != 'all':
        return response
    if not hasattr(response, 'get_json') or response.status_code != 200:
        return response
    payload = response.get_json()
    pages = [payload['data']]
    page_count = 1
    rows = len(payload['data'])
    deadline = time.monotonic() + 1
    while (args.get('range') == 'all' and payload.get('next_to') and
           page_count < batch and rows < 50000 and time.monotonic() < deadline):
        args['to'] = payload['next_to']
        args.pop('refresh', None)
        older = _chart_history_page(args, cached_only=True)
        if older is None or not hasattr(older, 'get_json') or older.status_code != 200:
            break
        data = older.get_json()
        pages.append(data['data'])
        rows += len(data['data'])
        page_count += 1
        payload['next_to'] = data['next_to']
        payload['complete'] = data['complete']
    payload['data'] = [row for page in reversed(pages) for row in page]
    payload['pages_loaded'] = page_count
    payload['cached_pages'] = page_count - (0 if payload['cache_hit'] else 1)
    return jsonify(payload)


def _chart_history_page(args, cached_only=False):
    """Generic historical OHLCV endpoint for any NSE/index symbol."""
    ue     = _ue()
    symbol = args.get("symbol", "").upper().strip()
    interval = (args.get("interval", "day") or "day").lower()
    if interval not in {"minute","3minute","5minute","10minute","15minute","30minute","60minute","day","week","month"}:
        interval = "day"

    all_history = args.get("range") == "all"
    sessions = args.get("sessions")
    if all_history and sessions:
        return jsonify(ok=False, error='Choose all history or a session limit, not both.'), 400
    if sessions is not None and (sessions != "3" or interval in ("week", "month")):
        return jsonify(ok=False, error='Use sessions=3 with an intraday or daily timeframe.'), 400
    if ue.state.kite_auth_error or not ue.broker.kite.access_token:
        return jsonify(ok=False, error='Connect Kite to load market history.'), 409
    today        = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=5, minutes=30))).date()
    default_days = {"minute": 30, "day": 365, "5minute": 30, "15minute": 60, "30minute": 90, "60minute": 180}
    default_from = today - datetime.timedelta(days=default_days.get(interval, 365))
    try:
        from_dt = datetime.date.fromisoformat(args.get("from", str(default_from)))
        to_dt   = datetime.date.fromisoformat(args.get("to",   str(today)))
    except ValueError:
        return jsonify({"ok": False, "error": "Invalid date"}), 400

    if not all_history and from_dt > to_dt:
        return jsonify(ok=False, error='Start date must not follow end date.'), 400
    history_floor = datetime.date(1970, 1, 1)
    next_to = None
    if all_history:
        if to_dt < history_floor or to_dt > today:
            return jsonify(ok=False, error='History cursor must be between 1970 and today.'), 400
        page_days = {"minute": 30, "3minute": 90, "5minute": 90, "10minute": 90,
                     "15minute": 180, "30minute": 180, "60minute": 365}.get(interval, 1900)
        from_dt = max(history_floor, to_dt - datetime.timedelta(days=page_days - 1))
    if sessions:
        # Search actual broker sessions, including weekends and exchange holidays.
        from_dt = to_dt - datetime.timedelta(days=30)
    if interval == "week":
        from_dt -= datetime.timedelta(days=from_dt.weekday())
    elif interval == "month":
        from_dt = from_dt.replace(day=1)

    if all_history:
        from_dt = max(history_floor, from_dt)
        if from_dt > history_floor:
            next_to = str(from_dt - datetime.timedelta(days=1))
    if not symbol:
        return jsonify({"ok": False, "error": "symbol required"}), 400

    KNOWN = {"NIFTY 50": 256265, "NIFTY50": 256265, "NIFTY": 256265,
             "BANKNIFTY": 260105, "BANK NIFTY": 260105, "SENSEX": 265}
    token = KNOWN.get(symbol) or (ue.config.index_token if symbol in ("", "INDEX") else None)

    if not token:
        global _nse_inst_cache
        try:
            today_s = str(today)
            if _nse_inst_cache["date"] != today_s or not _nse_inst_cache["data"]:
                _nse_inst_cache = {"data": ue.broker.kite.instruments("NSE"), "date": today_s}
            for inst in _nse_inst_cache["data"]:
                if inst.get("tradingsymbol") == symbol and inst.get("instrument_type") == "EQ":
                    token = inst["instrument_token"]
                    break
        except Exception as e:
            return jsonify({"ok": False, "error": f"Lookup failed: {e}"}), 500

    if not token:
        return jsonify({"ok": False, "error": f"Symbol '{symbol}' not found"}), 404

    try:
        fetch_interval = "day" if interval in ("week", "month") else interval
        def fetch():
            if all_history:
                return _history_page(ue.broker, token, from_dt, to_dt, fetch_interval, ue.state)
            return ue.broker.get_historical_data(token, f"{from_dt} 09:15:00", f"{to_dt} 15:30:00", fetch_interval, state=ue.state)
        from execution.history_cache import CacheMiss
        try:
            records, cache_hit, history_as_of = _chart_records(
                token, symbol, from_dt, to_dt, fetch_interval, fetch,
                bypass=args.get('refresh') == '1', cached_only=cached_only)
        except CacheMiss:
            return None
        # Only server-resolved chart instruments can later be subscribed by token.
        from dashboard.stream_routes import register_chart_token
        register_chart_token(ue, token)
        pagination = {"next_to": next_to, "complete": next_to is None} if all_history else {}
        records = sorted(records, key=lambda r: str(r["date"]))
        session_dates = sorted({_to_date(r["date"]) for r in records})
        if sessions:
            session_dates = session_dates[-3:]
            records = [r for r in records if _to_date(r["date"]) in session_dates]
        if interval in ("week", "month"):
            return jsonify(ok=True, data=_aggregate_candles(records, interval), symbol=symbol, interval=interval, token=token, cache_hit=cache_hit, history_as_of=history_as_of, **pagination)
        candles = []
        for r in records:
            d = _to_date(r["date"])
            if interval == "day":
                candles.append({"time": _day_key(d), "open": r["open"], "high": r["high"],
                                 "low": r["low"], "close": r["close"], "volume": r.get("volume", 0)})
            else:
                import datetime as _dt
                dt = r["date"]
                ts = int(dt.timestamp()) if hasattr(dt, "timestamp") else int(_dt.datetime.fromisoformat(str(dt)).timestamp())
                candles.append({"time": ts, "open": r["open"], "high": r["high"],
                                 "low": r["low"], "close": r["close"], "volume": r.get("volume", 0)})
        return jsonify({"ok": True, "data": candles, "symbol": symbol, "interval": interval, "token":token, "cache_hit":cache_hit, "history_as_of":history_as_of, "session_dates": [str(d) for d in session_dates], **pagination})
    except Exception as e:
        from execution.broker import is_kite_auth_error
        if is_kite_auth_error(e):
            ue.state.kite_auth_error = True
        return jsonify({"ok": False, "error": str(e)}), 500


@dashboard_bp.route("/api/nifty/history")
@jwt_required()
def nifty_history():
    ue       = _ue()
    interval = (request.args.get("interval", "day") or "day").lower()
    if interval not in ("day", "week", "month"):
        interval = "day"

    if ue.state.kite_auth_error or not ue.broker.kite.access_token:
        return jsonify(ok=False, error='Connect Kite to load market history.'), 409
    today        = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=5, minutes=30))).date()
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
                 "low":   r["low"],  "close": r["close"], "volume": r.get("volume", 0)}
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


def _backtest_config():
    """Historical runs use the saved BACKTEST settings, independent of live state."""
    from config.config_utils import get_mode_settings
    from db.database import SessionLocal
    from db.models import User
    cfg = TradingConfig()
    with SessionLocal() as db:
        user = db.get(User, _uid())
        apply_config_dict(cfg, get_mode_settings(user.settings_json or '', 'BACKTEST'))
    return cfg
