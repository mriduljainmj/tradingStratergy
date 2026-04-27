import datetime

from flask import Blueprint, jsonify, render_template, request

from core.state import BotState

dashboard_bp = Blueprint("dashboard", __name__)
_state: BotState = None
_switch_mode_callback = None
_backtester = None

VALID_MODES = {"BACKTEST", "PAPER", "LIVE"}


def register_state(state: BotState):
    global _state
    _state = state


def register_mode_switcher(callback):
    global _switch_mode_callback
    _switch_mode_callback = callback


def register_backtester(backtester):
    global _backtester
    _backtester = backtester


@dashboard_bp.route("/")
def index():
    return render_template("dashboard.html", mode=_state.app_mode)


@dashboard_bp.route("/api/state")
def get_state():
    return jsonify(_state.to_dict())


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
