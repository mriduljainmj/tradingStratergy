import datetime
import math
from collections import defaultdict

from flask import Blueprint, jsonify, render_template, request
from flask_jwt_extended import get_jwt_identity, jwt_required

from db.database import SessionLocal
from db.models import Trade

analytics_bp = Blueprint("analytics", __name__)

VALID_MODES = {"PAPER", "LIVE", "ALL"}


def _uid():
    return int(get_jwt_identity())


def _bad(msg, code=400):
    return jsonify({"ok": False, "error": msg}), code


def _mode_filter(q, mode: str):
    """Apply a trade_mode filter to a SQLAlchemy query."""
    if mode and mode.upper() in ("PAPER", "LIVE"):
        q = q.filter(Trade.trade_mode == mode.upper())
    return q


# ── Pages ──────────────────────────────────────────────────────────────────────

@analytics_bp.route("/analytics")
def analytics_page():
    return render_template("analytics.html")


# ── Save a completed trade (called from trading engine) ───────────────────────

@analytics_bp.route("/api/trades", methods=["POST"])
@jwt_required()
def save_trade():
    data = request.get_json(silent=True) or {}
    db   = SessionLocal()
    try:
        date_val = datetime.date.today()
        if data.get("date"):
            try:
                date_val = datetime.date.fromisoformat(data["date"])
            except ValueError:
                pass

        mode = (data.get("trade_mode") or "PAPER").upper()
        if mode not in ("PAPER", "LIVE"):
            mode = "PAPER"

        trade = Trade(
            user_id       = _uid(),
            date          = date_val,
            trade_mode    = mode,
            symbol        = data.get("symbol", "NIFTY"),
            position_type = data.get("position_type"),
            entry_time    = _parse_dt(data.get("entry_time")),
            exit_time     = _parse_dt(data.get("exit_time")),
            entry_prem    = data.get("entry_prem"),
            exit_prem     = data.get("exit_prem"),
            strike        = data.get("strike"),
            quantity      = data.get("quantity"),
            gross_pnl     = data.get("gross_pnl"),
            charges       = data.get("charges"),
            net_pnl       = data.get("net_pnl"),
            exit_reason   = data.get("exit_reason"),
            or_high       = data.get("or_high"),
            or_low        = data.get("or_low"),
        )
        db.add(trade)
        db.commit()
        db.refresh(trade)
        return jsonify({"ok": True, "trade": trade.to_dict()}), 201
    except Exception as e:
        db.rollback()
        return _bad(str(e), 500)
    finally:
        db.close()


def _parse_dt(val):
    if not val:
        return None
    try:
        return datetime.datetime.fromisoformat(val)
    except Exception:
        return None


# ── List trades ───────────────────────────────────────────────────────────────

@analytics_bp.route("/api/trades")
@jwt_required()
def list_trades():
    page      = max(1, int(request.args.get("page", 1)))
    per_page  = min(100, int(request.args.get("per_page", 20)))
    from_dt   = request.args.get("from")
    to_dt     = request.args.get("to")
    mode      = request.args.get("mode", "ALL").upper()

    db = SessionLocal()
    try:
        q = db.query(Trade).filter_by(user_id=_uid())
        q = _mode_filter(q, mode)
        if from_dt:
            q = q.filter(Trade.date >= datetime.date.fromisoformat(from_dt))
        if to_dt:
            q = q.filter(Trade.date <= datetime.date.fromisoformat(to_dt))
        q = q.order_by(Trade.date.desc(), Trade.id.desc())

        total  = q.count()
        trades = q.offset((page - 1) * per_page).limit(per_page).all()
        return jsonify({
            "ok": True,
            "total": total,
            "page": page,
            "per_page": per_page,
            "trades": [t.to_dict() for t in trades],
        })
    finally:
        db.close()


# ── Analytics summary ─────────────────────────────────────────────────────────

@analytics_bp.route("/api/analytics/summary")
@jwt_required()
def summary():
    from_dt = request.args.get("from")
    to_dt   = request.args.get("to")
    mode    = request.args.get("mode", "ALL").upper()

    db = SessionLocal()
    try:
        q = db.query(Trade).filter_by(user_id=_uid())
        q = _mode_filter(q, mode)
        if from_dt:
            q = q.filter(Trade.date >= datetime.date.fromisoformat(from_dt))
        if to_dt:
            q = q.filter(Trade.date <= datetime.date.fromisoformat(to_dt))
        trades = q.order_by(Trade.date).all()

        if not trades:
            empty = _empty_summary()
            return jsonify({"ok": True, "data": {}, **empty})

        return jsonify({"ok": True, "data": {}, **_compute_summary(trades)})
    finally:
        db.close()


def _compute_summary(trades: list) -> dict:
    pnls        = [t.net_pnl for t in trades if t.net_pnl is not None]
    wins        = [p for p in pnls if p > 0]
    losses      = [p for p in pnls if p <= 0]
    total       = len(pnls)
    win_rate    = round(len(wins) / total * 100, 2) if total else 0
    gross_sum   = sum(t.gross_pnl or 0 for t in trades)
    charges_sum = sum(t.charges   or 0 for t in trades)
    net_sum     = sum(pnls)
    avg_win     = round(sum(wins)   / len(wins),   2) if wins   else 0
    avg_loss    = round(sum(losses) / len(losses), 2) if losses else 0

    win_total    = sum(wins)
    loss_total   = abs(sum(losses))
    profit_factor = round(win_total / loss_total, 2) if loss_total else float("inf")

    # Max drawdown
    peak = cumulative = max_dd = 0
    for p in pnls:
        cumulative += p
        peak = max(peak, cumulative)
        max_dd = min(max_dd, cumulative - peak)

    max_dd_pct = round(max_dd / peak * 100, 2) if peak > 0 else 0

    # Sharpe (annualised daily)
    daily: dict[datetime.date, float] = defaultdict(float)
    for t in trades:
        if t.date and t.net_pnl is not None:
            daily[t.date] += t.net_pnl
    daily_returns = list(daily.values())
    sharpe = 0.0
    if len(daily_returns) > 1:
        mean = sum(daily_returns) / len(daily_returns)
        std  = math.sqrt(sum((r - mean) ** 2 for r in daily_returns) / (len(daily_returns) - 1))
        sharpe = round((mean / std) * math.sqrt(252), 2) if std else 0

    return {
        "total_trades":    total,
        "winning_trades":  len(wins),
        "losing_trades":   len(losses),
        "win_rate":        win_rate,
        "total_net_pnl":   round(net_sum, 2),
        "total_gross_pnl": round(gross_sum, 2),
        "total_charges":   round(charges_sum, 2),
        "avg_win":         avg_win,
        "avg_loss":        avg_loss,
        "profit_factor":   profit_factor,
        "max_drawdown":    round(max_dd, 2),
        "max_drawdown_pct": max_dd_pct,
        "best_trade":      round(max(pnls), 2) if pnls else 0,
        "worst_trade":     round(min(pnls), 2) if pnls else 0,
        "avg_trade":       round(net_sum / total, 2) if total else 0,
        "sharpe_ratio":    sharpe,
    }


def _empty_summary():
    return {k: 0 for k in [
        "total_trades","winning_trades","losing_trades","win_rate","total_net_pnl",
        "total_gross_pnl","total_charges","avg_win","avg_loss","profit_factor",
        "max_drawdown","max_drawdown_pct","best_trade","worst_trade","avg_trade","sharpe_ratio"
    ]}


# ── Equity curve ──────────────────────────────────────────────────────────────

@analytics_bp.route("/api/analytics/equity-curve")
@jwt_required()
def equity_curve():
    from_dt = request.args.get("from")
    to_dt   = request.args.get("to")
    mode    = request.args.get("mode", "ALL").upper()

    db = SessionLocal()
    try:
        q = db.query(Trade).filter_by(user_id=_uid())
        q = _mode_filter(q, mode)
        if from_dt:
            q = q.filter(Trade.date >= datetime.date.fromisoformat(from_dt))
        if to_dt:
            q = q.filter(Trade.date <= datetime.date.fromisoformat(to_dt))
        trades = q.order_by(Trade.date, Trade.id).all()

        daily: dict[datetime.date, float] = defaultdict(float)
        for t in trades:
            if t.date and t.net_pnl is not None:
                daily[t.date] += t.net_pnl

        cumulative = 0
        curve = []
        for d in sorted(daily):
            cumulative += daily[d]
            curve.append({
                "date":           d.isoformat(),
                "time":           int(datetime.datetime.combine(d, datetime.time()).timestamp()),
                "daily_pnl":      round(daily[d], 2),
                "cumulative_pnl": round(cumulative, 2),
            })
        return jsonify({"ok": True, "data": curve})
    finally:
        db.close()


# ── Monthly breakdown ─────────────────────────────────────────────────────────

@analytics_bp.route("/api/analytics/monthly")
@jwt_required()
def monthly():
    mode = request.args.get("mode", "ALL").upper()
    db   = SessionLocal()
    try:
        q = db.query(Trade).filter_by(user_id=_uid())
        q = _mode_filter(q, mode)
        trades = q.order_by(Trade.date).all()

        months: dict[str, dict] = defaultdict(lambda: {"trades":0,"wins":0,"net_pnl":0.0})
        for t in trades:
            if not t.date or t.net_pnl is None:
                continue
            key = t.date.strftime("%Y-%m")
            months[key]["trades"] += 1
            months[key]["net_pnl"] += t.net_pnl
            if t.net_pnl > 0:
                months[key]["wins"] += 1

        result = []
        for month in sorted(months):
            m = months[month]
            result.append({
                "month":    month,
                "trades":   m["trades"],
                "net_pnl":  round(m["net_pnl"], 2),
                "win_rate": round(m["wins"] / m["trades"] * 100, 1) if m["trades"] else 0,
            })
        return jsonify({"ok": True, "data": result})
    finally:
        db.close()


# ── Side-by-side comparison (Paper vs Live) ───────────────────────────────────

@analytics_bp.route("/api/analytics/compare")
@jwt_required()
def compare():
    """Returns summary stats for PAPER and LIVE side-by-side."""
    from_dt = request.args.get("from")
    to_dt   = request.args.get("to")
    db      = SessionLocal()
    try:
        result = {}
        for mode in ("PAPER", "LIVE"):
            q = db.query(Trade).filter_by(user_id=_uid())
            q = _mode_filter(q, mode)
            if from_dt:
                q = q.filter(Trade.date >= datetime.date.fromisoformat(from_dt))
            if to_dt:
                q = q.filter(Trade.date <= datetime.date.fromisoformat(to_dt))
            trades = q.order_by(Trade.date).all()
            result[mode.lower()] = _compute_summary(trades) if trades else _empty_summary()
        return jsonify({"ok": True, "data": result})
    finally:
        db.close()
