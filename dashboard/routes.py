import datetime

from flask import Blueprint, jsonify, render_template, request

from config.settings import TradingConfig
from core.state import BotState

dashboard_bp = Blueprint("dashboard", __name__)
_state: BotState = None
_switch_mode_callback = None
_backtester = None
_trading_config: TradingConfig = None

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

    if mode == "single":
        date_str = data.get("date", "")
        try:
            date = datetime.date.fromisoformat(date_str)
        except ValueError:
            return jsonify({"error": "Invalid date. Use YYYY-MM-DD format."}), 400
        return jsonify(_backtester.run_day(date))

    elif mode == "range":
        try:
            from_date = datetime.date.fromisoformat(data.get("from_date", ""))
            to_date = datetime.date.fromisoformat(data.get("to_date", ""))
        except ValueError:
            return jsonify({"error": "Invalid dates. Use YYYY-MM-DD format."}), 400
        return jsonify(_backtester.run_range(from_date, to_date))

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
