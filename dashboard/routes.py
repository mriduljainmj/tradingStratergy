import datetime
import logging

from flask import Blueprint, jsonify, render_template, request

logger = logging.getLogger(__name__)

from config.settings import TradingConfig
from core.state import BotState

dashboard_bp = Blueprint("dashboard", __name__)
_state: BotState = None
_switch_mode_callback = None
_backtester = None
_trading_config: TradingConfig = None
_broker = None
_start_engine_fn = None
_initial_mode: str = "PAPER"

VALID_MODES = {"BACKTEST", "PAPER", "LIVE"}

# Fields exposed via the settings API, grouped by section
_STRATEGY_FIELDS = ["target_pts", "fib_trail", "entry_end_time", "eod_exit_time", "strike_spacing"]
_POSITION_FIELDS = ["lot_size", "qty_multiplier"]
_OPTIONS_FIELDS  = ["risk_free_rate", "assumed_iv"]
_BROKER_FIELDS   = ["brokerage_per_order", "stt_pct", "exchange_charges_pct",
                    "gst_pct", "sebi_charges_pct", "stamp_duty_pct"]
_TIME_FIELDS     = {"entry_end_time", "eod_exit_time"}


def register_state(state: BotState):
    global _state
    _state = state


def register_mode_switcher(callback):
    global _switch_mode_callback
    _switch_mode_callback = callback


def register_backtester(backtester):
    global _backtester
    _backtester = backtester


def register_trading_config(config: TradingConfig):
    global _trading_config
    _trading_config = config


def register_broker(broker):
    global _broker
    _broker = broker


def register_start_engine(fn, initial_mode: str = "PAPER"):
    global _start_engine_fn, _initial_mode
    _start_engine_fn = fn
    _initial_mode = initial_mode


def _config_to_dict(cfg: TradingConfig) -> dict:
    result = {}
    for field in _STRATEGY_FIELDS + _POSITION_FIELDS + _OPTIONS_FIELDS + _BROKER_FIELDS:
        val = getattr(cfg, field, None)
        if isinstance(val, datetime.time):
            val = val.strftime("%H:%M")
        result[field] = val
    return result


def _apply_config_dict(cfg: TradingConfig, data: dict):
    for field in _STRATEGY_FIELDS + _POSITION_FIELDS + _OPTIONS_FIELDS + _BROKER_FIELDS:
        if field not in data:
            continue
        val = data[field]
        if field in _TIME_FIELDS:
            try:
                h, m = str(val).split(":")
                setattr(cfg, field, datetime.time(int(h), int(m)))
            except Exception:
                pass
        else:
            current = getattr(cfg, field, None)
            try:
                setattr(cfg, field, type(current)(val))
            except Exception:
                pass


@dashboard_bp.route("/")
def index():
    return render_template("dashboard.html", mode=_state.app_mode)


@dashboard_bp.route("/api/state")
def get_state():
    return jsonify(_state.to_dict())


@dashboard_bp.route("/api/balance")
def get_balance():
    """Returns real-time funds from Kite (LIVE) or state balance (PAPER)."""
    if not _broker:
        return jsonify({"available": 0.0, "used": 0.0, "total": 0.0})
    if _state and _state.app_mode == "LIVE":
        funds = _broker.get_funds()
        _state.balance = funds["available"]
        return jsonify(funds)
    # PAPER / BACKTEST — return simulated balance from state
    return jsonify({"available": _state.balance if _state else 0.0,
                    "used": 0.0, "total": _state.balance if _state else 0.0})


@dashboard_bp.route("/api/settings", methods=["GET"])
def get_settings():
    if not _trading_config:
        return jsonify({"error": "Config not registered"}), 503
    return jsonify(_config_to_dict(_trading_config))


@dashboard_bp.route("/api/settings", methods=["POST"])
def update_settings():
    if not _trading_config:
        return jsonify({"error": "Config not registered"}), 503
    data = request.json or {}
    _apply_config_dict(_trading_config, data)
    return jsonify({"ok": True, "settings": _config_to_dict(_trading_config)})


@dashboard_bp.route("/api/backtest/run", methods=["POST"])
def run_historical_backtest():
    if not _backtester:
        return jsonify({"error": "Backtester not available"}), 503

    data = request.json or {}
    mode = data.get("mode", "single")

    def _check_auth_error(result: dict):
        """If the backtest failed with a Kite auth error, flag it on shared state."""
        err = result.get("error", "")
        if err and _state and _broker:
            from execution.broker import is_kite_auth_error
            if is_kite_auth_error(Exception(err)):
                _state.kite_auth_error = True

    if mode == "single":
        date_str = data.get("date", "")
        try:
            date = datetime.date.fromisoformat(date_str)
        except ValueError:
            return jsonify({"error": "Invalid date. Use YYYY-MM-DD format."}), 400
        result = _backtester.run_day(date)
        _check_auth_error(result)
        return jsonify(result)

    elif mode == "range":
        try:
            from_date = datetime.date.fromisoformat(data.get("from_date", ""))
            to_date = datetime.date.fromisoformat(data.get("to_date", ""))
        except ValueError:
            return jsonify({"error": "Invalid dates. Use YYYY-MM-DD format."}), 400
        result = _backtester.run_range(from_date, to_date)
        _check_auth_error(result)
        return jsonify(result)

    return jsonify({"error": "mode must be 'single' or 'range'"}), 400


@dashboard_bp.route("/api/mode", methods=["POST"])
def switch_mode():
    new_mode = (request.json or {}).get("mode", "").upper()
    if new_mode not in VALID_MODES:
        return jsonify({"error": f"Invalid mode. Must be one of {VALID_MODES}"}), 400
    if new_mode == _state.app_mode:
        return jsonify({"mode": new_mode, "changed": False})
    if _switch_mode_callback:
        _switch_mode_callback(new_mode)
    return jsonify({"mode": new_mode, "changed": True})



@dashboard_bp.route("/api/option-chart")
def option_chart():
    """
    Fetch OHLC candles for any NIFTY option strike on a given date.
    Query params: strike (int), type (CE|PE), date (YYYY-MM-DD), interval (minute|5minute|day)
    """
    if not _broker:
        return jsonify({"ok": False, "error": "Broker not available"}), 503

    strike   = request.args.get("strike", type=int)
    opt_type = (request.args.get("type", "CE") or "CE").upper()
    date_str = request.args.get("date", "")
    interval = request.args.get("interval", "minute")

    if not strike:
        return jsonify({"ok": False, "error": "strike is required"}), 400
    if opt_type not in ("CE", "PE"):
        return jsonify({"ok": False, "error": "type must be CE or PE"}), 400

    try:
        trade_date = datetime.date.fromisoformat(date_str) if date_str else datetime.date.today()
    except ValueError:
        return jsonify({"ok": False, "error": "Invalid date format"}), 400

    try:
        records = _broker.get_option_history(strike, opt_type, trade_date, interval)
        candles = [
            {
                "time":  int(r["date"].timestamp()),
                "open":  r["open"], "high": r["high"],
                "low":   r["low"],  "close": r["close"],
            }
            for r in records
        ]
        # Build label
        label = f"NIFTY {strike} {opt_type}"
        return jsonify({"ok": True, "data": candles, "label": label})
    except Exception as e:
        if _state and _broker:
            from execution.broker import is_kite_auth_error
            if is_kite_auth_error(e):
                _state.kite_auth_error = True
        return jsonify({"ok": False, "error": str(e)}), 500


@dashboard_bp.route("/api/nifty/history")
def nifty_history():
    """
    Fetch NIFTY 50 candles for day / week / month view.
    Query params:
      interval = day | week | month
      from     = YYYY-MM-DD  (default: 365 days ago)
      to       = YYYY-MM-DD  (default: today)
    """
    if not _broker:
        return jsonify({"ok": False, "error": "Broker not available"}), 503

    interval = (request.args.get("interval", "day") or "day").lower()
    _kite_interval_map = {"day": "day", "week": "week", "month": "month"}
    kite_interval = _kite_interval_map.get(interval, "day")

    today = datetime.date.today()
    default_days = {"day": 365, "week": 730, "month": 1825}
    default_from = today - datetime.timedelta(days=default_days.get(interval, 365))

    try:
        from_dt = datetime.date.fromisoformat(request.args.get("from", str(default_from)))
        to_dt   = datetime.date.fromisoformat(request.args.get("to",   str(today)))
    except ValueError:
        return jsonify({"ok": False, "error": "Invalid date format"}), 400

    try:
        records = _broker.get_historical_data(
            _trading_config.index_token,
            f"{from_dt} 09:15:00",
            f"{to_dt} 15:30:00",
            kite_interval,
            state=_state,
        )
        candles = [
            {
                "time":  int(r["date"].timestamp()),
                "open":  r["open"], "high": r["high"],
                "low":   r["low"],  "close": r["close"],
            }
            for r in records
        ]
        return jsonify({"ok": True, "data": candles, "interval": interval})
    except Exception as e:
        if _state:
            from execution.broker import is_kite_auth_error
            if is_kite_auth_error(e):
                _state.kite_auth_error = True
        return jsonify({"ok": False, "error": str(e)}), 500


