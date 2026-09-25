import datetime
import logging
import math
import re
from collections import defaultdict

from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required

from db.database import SessionLocal
from db.models import Trade

logger = logging.getLogger(__name__)

analytics_bp = Blueprint("analytics", __name__)

VALID_MODES = {"PAPER", "LIVE", "ALL", "IMPORT"}


def _uid():
    return int(get_jwt_identity())


def _bad(msg, code=400):
    return jsonify({"ok": False, "error": msg}), code


def _mode_filter(q, mode: str):
    """Apply trade_mode + optional per-strategy filters to a query.

    Every analytics endpoint funnels through here, so supporting the
    ?strategy_id= query param in one place gives the whole Results page
    (summary, equity curve, monthly, trade log) per-strategy filtering.
    """
    if mode and mode.upper() in ("PAPER", "LIVE", "IMPORT"):
        q = q.filter(Trade.trade_mode == mode.upper())
    sid = (request.args.get("strategy_id") or "").strip()
    if sid.isdigit():
        q = q.filter(Trade.strategy_id == int(sid))
    return q


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
    per_page  = max(1, min(100, int(request.args.get("per_page", 20))))
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
    profit_factor = round(win_total / loss_total, 2) if loss_total else None

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
                "time":           int(datetime.datetime.combine(d, datetime.time(), tzinfo=datetime.timezone(datetime.timedelta(hours=5, minutes=30))).timestamp()),
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
        try:
            from_dt = request.args.get("from")
            to_dt = request.args.get("to")
            if from_dt:
                q = q.filter(Trade.date >= datetime.date.fromisoformat(from_dt))
            if to_dt:
                q = q.filter(Trade.date <= datetime.date.fromisoformat(to_dt))
        except ValueError:
            return _bad("Dates must use YYYY-MM-DD format.")
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


# ══════════════════════════════════════════════════════════════════════════════
# DEEP INSIGHTS ENGINE
# ══════════════════════════════════════════════════════════════════════════════

@analytics_bp.route("/api/analytics/insights")
@jwt_required()
def insights():
    """
    Comprehensive behavioural analytics & advanced risk metrics.

    Returns:
      expectancy, sortino, VaR, streak analysis, holding-time leakage,
      day-of-week P&L, entry time-block P&L, brokerage bleed, yearly breakdown.
    """
    mode    = request.args.get("mode", "ALL").upper()
    from_dt = request.args.get("from")
    to_dt   = request.args.get("to")

    db = SessionLocal()
    try:
        q = db.query(Trade).filter_by(user_id=_uid())
        q = _mode_filter(q, mode)
        if from_dt:
            q = q.filter(Trade.date >= datetime.date.fromisoformat(from_dt))
        if to_dt:
            q = q.filter(Trade.date <= datetime.date.fromisoformat(to_dt))
        trades = q.order_by(Trade.date, Trade.id).all()

        if not trades:
            return jsonify({"ok": True, "empty": True, "data": {}})

        return jsonify({"ok": True, "empty": False, "data": _compute_insights(trades)})
    finally:
        db.close()


def _compute_insights(trades: list) -> dict:
    """Run all advanced analytics on a list of Trade ORM objects."""

    # ── 1. Build core arrays ──────────────────────────────────────────────────
    pnls        = [t.net_pnl    for t in trades if t.net_pnl    is not None]
    gross_pnls  = [t.gross_pnl  for t in trades if t.gross_pnl  is not None]
    charges_arr = [t.charges    for t in trades if t.charges    is not None]

    wins   = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    total  = len(pnls)
    if total == 0:
        return {}

    win_rate  = len(wins)  / total
    loss_rate = 1 - win_rate
    avg_win   = sum(wins)   / len(wins)   if wins   else 0.0
    avg_loss  = sum(losses) / len(losses) if losses else 0.0

    # ── 2. Expectancy per trade ───────────────────────────────────────────────
    # Expectancy = (Win% × Avg Win) + (Loss% × Avg Loss)  [avg_loss is ≤ 0]
    expectancy = (win_rate * avg_win) + (loss_rate * avg_loss)

    # ── 3. Daily returns for ratio metrics ───────────────────────────────────
    daily_map: dict[datetime.date, float] = defaultdict(float)
    for t in trades:
        if t.date and t.net_pnl is not None:
            daily_map[t.date] += t.net_pnl
    daily_returns = list(daily_map.values())

    risk_free_daily = 0.065 / 252   # 6.5% annualised

    sortino = 0.0
    var_95  = 0.0
    if len(daily_returns) > 1:
        mean_r  = sum(daily_returns) / len(daily_returns)
        # Sortino: downside deviation only (returns below risk-free)
        neg     = [r for r in daily_returns if r < risk_free_daily]
        if neg:
            down_dev = math.sqrt(sum((r - 0) ** 2 for r in neg) / len(neg))
            sortino  = round((mean_r - risk_free_daily) / down_dev * math.sqrt(252), 2) if down_dev else 0.0
        # Historical VaR at 95% confidence (5th percentile of losses)
        sorted_r = sorted(daily_returns)
        idx      = max(0, int(len(sorted_r) * 0.05) - 1)
        var_95   = round(sorted_r[idx], 2)

    # ── 4. Streak analysis ───────────────────────────────────────────────────
    max_win_streak = max_loss_streak = 0
    cur_win = cur_loss = 0
    for p in pnls:
        if p > 0:
            cur_win   += 1
            cur_loss   = 0
            max_win_streak  = max(max_win_streak,  cur_win)
        else:
            cur_loss  += 1
            cur_win    = 0
            max_loss_streak = max(max_loss_streak, cur_loss)

    # ── 5. Holding-time leakage ───────────────────────────────────────────────
    # Average holding duration (minutes) for winning vs losing trades
    win_durations  = []
    loss_durations = []
    for t in trades:
        if t.entry_time and t.exit_time and t.net_pnl is not None:
            dur = (t.exit_time - t.entry_time).total_seconds() / 60
            if dur > 0:
                (win_durations if t.net_pnl > 0 else loss_durations).append(dur)

    avg_hold_win  = round(sum(win_durations)  / len(win_durations),  1) if win_durations  else None
    avg_hold_loss = round(sum(loss_durations) / len(loss_durations), 1) if loss_durations else None

    # ── 6. Day-of-week analysis ───────────────────────────────────────────────
    DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
    dow_map: dict[int, list] = {i: [] for i in range(5)}
    for t in trades:
        if t.date and t.net_pnl is not None:
            wd = t.date.weekday()  # 0=Mon … 4=Fri
            if wd < 5:
                dow_map[wd].append(t.net_pnl)

    dow_stats = []
    for i in range(5):
        ps = dow_map[i]
        if not ps:
            dow_stats.append({"day": DAYS[i], "trades": 0, "net_pnl": 0, "win_rate": 0, "avg_pnl": 0})
        else:
            w = [p for p in ps if p > 0]
            dow_stats.append({
                "day":      DAYS[i],
                "trades":   len(ps),
                "net_pnl":  round(sum(ps), 2),
                "win_rate": round(len(w) / len(ps) * 100, 1),
                "avg_pnl":  round(sum(ps) / len(ps), 2),
            })

    # ── 7. Entry time-block analysis ─────────────────────────────────────────
    # Blocks: Opening Rush 9:15-10:00, Mid-Morning 10:00-11:30,
    #         Midday 11:30-13:30, Closing 13:30-15:30
    time_blocks = [
        ("Opening Rush",  datetime.time(9, 15),  datetime.time(10,  0)),
        ("Mid-Morning",   datetime.time(10,  0), datetime.time(11, 30)),
        ("Midday",        datetime.time(11, 30), datetime.time(13, 30)),
        ("Closing Rush",  datetime.time(13, 30), datetime.time(15, 30)),
    ]
    block_map: dict[str, list] = {b[0]: [] for b in time_blocks}
    for t in trades:
        if t.entry_time and t.net_pnl is not None:
            et = t.entry_time.time() if hasattr(t.entry_time, "time") else t.entry_time
            for name, start, end in time_blocks:
                if start <= et < end:
                    block_map[name].append(t.net_pnl)
                    break

    time_block_stats = []
    for name, _, _ in time_blocks:
        ps = block_map[name]
        if not ps:
            time_block_stats.append({"block": name, "trades": 0, "net_pnl": 0, "win_rate": 0, "avg_pnl": 0})
        else:
            w = [p for p in ps if p > 0]
            time_block_stats.append({
                "block":    name,
                "trades":   len(ps),
                "net_pnl":  round(sum(ps), 2),
                "win_rate": round(len(w) / len(ps) * 100, 1),
                "avg_pnl":  round(sum(ps) / len(ps), 2),
            })

    # ── 8. Brokerage bleed ───────────────────────────────────────────────────
    total_gross   = sum(p for p in gross_pnls if p > 0)
    total_charges = sum(charges_arr)
    bleed_pct     = round(total_charges / total_gross * 100, 1) if total_gross > 0 else 0.0

    # ── 9. Yearly breakdown ───────────────────────────────────────────────────
    year_map: dict[int, list] = defaultdict(list)
    for t in trades:
        if t.date and t.net_pnl is not None:
            year_map[t.date.year].append(t)

    yearly = []
    for yr in sorted(year_map):
        yt = year_map[yr]
        yp = [t.net_pnl for t in yt]
        yw = [p for p in yp if p > 0]
        yl = [p for p in yp if p <= 0]
        yg = sum(t.gross_pnl or 0 for t in yt)
        yc = sum(t.charges   or 0 for t in yt)
        yearly.append({
            "year":         yr,
            "trades":       len(yp),
            "net_pnl":      round(sum(yp), 2),
            "gross_pnl":    round(yg, 2),
            "charges":      round(yc, 2),
            "win_rate":     round(len(yw) / len(yp) * 100, 1) if yp else 0,
            "avg_win":      round(sum(yw) / len(yw), 2) if yw else 0,
            "avg_loss":     round(sum(yl) / len(yl), 2) if yl else 0,
            "bleed_pct":    round(yc / sum(p for p in yg if p > 0) * 100, 1) if yg > 0 else 0,
        })
    # fix bleed_pct per year (yg is a float, not iterable)
    for row in yearly:
        if row["gross_pnl"] > 0:
            row["bleed_pct"] = round(row["charges"] / row["gross_pnl"] * 100, 1)
        else:
            row["bleed_pct"] = 0.0

    # ── 10. Auto Red Flags ────────────────────────────────────────────────────
    red_flags = []

    # Worst day of week
    worst_dow = min(dow_stats, key=lambda x: x["net_pnl"])
    if worst_dow["trades"] >= 3 and worst_dow["net_pnl"] < 0:
        red_flags.append(
            f"Your biggest losing day of the week is {worst_dow['day']} "
            f"(₹{worst_dow['net_pnl']:,.0f} across {worst_dow['trades']} trades)."
        )

    # Holding time leakage
    if avg_hold_win is not None and avg_hold_loss is not None:
        if avg_hold_loss > avg_hold_win * 1.5:
            red_flags.append(
                f"You hold losing trades {round(avg_hold_loss/avg_hold_win, 1)}× longer than winners "
                f"({avg_hold_loss:.0f} min vs {avg_hold_win:.0f} min) — classic 'cut winners, hold losers' pattern."
            )

    # Brokerage bleed warning
    if bleed_pct > 30:
        red_flags.append(
            f"Charges consumed {bleed_pct:.1f}% of your gross profits. "
            f"Consider reducing trade frequency or switching to a lower-brokerage plan."
        )

    # Worst time block
    worst_block = min(time_block_stats, key=lambda x: x["avg_pnl"])
    if worst_block["trades"] >= 3 and worst_block["avg_pnl"] < -500:
        red_flags.append(
            f"Your worst time block is {worst_block['block']} "
            f"(avg ₹{worst_block['avg_pnl']:,.0f}/trade, win rate {worst_block['win_rate']}%). "
            f"Consider avoiding entries in this window."
        )

    # Loss streak warning
    if max_loss_streak >= 5:
        red_flags.append(
            f"Maximum loss streak was {max_loss_streak} consecutive trades. "
            f"Consider a circuit-breaker rule after 3 consecutive losses."
        )

    # VaR warning
    if var_95 < -10_000:
        red_flags.append(
            f"Daily VaR (95%) is ₹{var_95:,.0f} — on your worst 5% of days "
            f"you lose more than this amount. Review position sizing."
        )

    return {
        # Core risk metrics
        "expectancy":        round(expectancy, 2),
        "sortino_ratio":     sortino,
        "var_95":            var_95,
        "max_win_streak":    max_win_streak,
        "max_loss_streak":   max_loss_streak,
        # Holding time
        "avg_hold_win_min":  avg_hold_win,
        "avg_hold_loss_min": avg_hold_loss,
        # Brokerage bleed
        "total_charges":     round(total_charges, 2),
        "total_gross_wins":  round(total_gross, 2),
        "bleed_pct":         bleed_pct,
        # Behavioural breakdowns
        "day_of_week":       dow_stats,
        "time_blocks":       time_block_stats,
        "yearly":            yearly,
        # Auto-generated flags
        "red_flags":         red_flags,
        # Summary stats for header
        "total_trades":      total,
        "win_rate_pct":      round(win_rate * 100, 1),
        "avg_win":           round(avg_win,  2),
        "avg_loss":          round(avg_loss, 2),
    }


# ── Console CSV / XLSX Import ─────────────────────────────────────────────────

@analytics_bp.route("/api/analytics/import-csv", methods=["POST"])
@jwt_required()
def import_csv():
    """
    Parse a Zerodha Console 'Tax P&L' or 'Trade Book' CSV/XLSX and store
    the trades in the DB with trade_mode='IMPORT'.

    Supported column schemas:
      • Console Tax P&L:  Symbol, Buy Date, Buy Qty, Buy Avg, Sell Date,
                          Sell Qty, Sell Avg, P&L, Charges/Tax
      • Console Trade Book: symbol, trade_date, trade_type, quantity, price
    """
    import io
    uid = _uid()

    if "file" not in request.files:
        return _bad("No file uploaded — attach a CSV or XLSX as 'file'.")

    f    = request.files["file"]
    name = (f.filename or "").lower()
    if not (name.endswith(".csv") or name.endswith(".xlsx") or name.endswith(".xls")):
        return _bad("Only .csv / .xlsx / .xls files are supported.")

    try:
        raw = f.read()
        if name.endswith(".csv"):
            try:
                df = _parse_csv(io.StringIO(raw.decode("utf-8-sig")))
            except UnicodeDecodeError:
                df = _parse_csv(io.StringIO(raw.decode("latin-1")))
        else:
            try:
                import pandas as pd
                df = pd.read_excel(io.BytesIO(raw), header=None)
                df = _detect_and_clean(df)
            except ImportError:
                return _bad("pandas/openpyxl not installed — upload a CSV instead.")

        if df is None or len(df) == 0:
            return _bad("File parsed successfully but no trade rows found.")

        inserted = _insert_imported_trades(df, uid)
        return jsonify({"ok": True, "inserted": inserted,
                        "message": f"Imported {inserted} trades from {f.filename}"})

    except Exception as e:
        logger.exception("CSV import failed")
        return _bad(f"Parse error: {e}", 500)


def _parse_csv(stream):
    """Try multiple Console CSV schemas and return a normalised DataFrame."""
    try:
        import pandas as pd
    except ImportError:
        raise RuntimeError("pandas is required for CSV import")

    # Read with no header assumption first to detect the schema
    raw = pd.read_csv(stream, header=None, dtype=str)
    return _detect_and_clean(raw)


def _detect_and_clean(raw):
    """
    Auto-detect the Console schema by scanning column headers,
    then return a normalised DataFrame with standardised columns.

    Normalised schema:
      trade_date, exit_date, symbol, position_type, quantity,
      buy_price, sell_price, gross_pnl, charges, net_pnl
    """
    import pandas as pd
    import numpy as np

    # Find the header row (first row where ≥3 cells contain alphabetic strings)
    header_row = 0
    for i, row in raw.iterrows():
        alpha_count = sum(1 for v in row if isinstance(v, str) and v.strip().isalpha() is False
                          and any(c.isalpha() for c in str(v)))
        if alpha_count >= 3:
            header_row = i
            break

    df = raw.iloc[header_row + 1:].copy()
    df.columns = [str(c).strip().lower() for c in raw.iloc[header_row]]
    df = df.dropna(how="all").reset_index(drop=True)

    cols = list(df.columns)

    # ── Schema A: Console Tax P&L ─────────────────────────────────────────
    # Columns: symbol, isin, quantity, buy date, buy value/avg, sell date, sell value/avg, p&l, ...
    if any(c for c in cols if "buy" in c and "date" in c):
        sym_col  = _find_col(cols, ["symbol", "scrip", "security"])
        bdate    = _find_col(cols, ["buy date", "purchase date"])
        bqty     = _find_col(cols, ["buy qty", "buy quantity", "quantity"])
        bavg     = _find_col(cols, ["buy avg", "buy price", "buy value"])
        sdate    = _find_col(cols, ["sell date", "sale date"])
        savg     = _find_col(cols, ["sell avg", "sell price", "sell value"])
        pnl_col  = _find_col(cols, ["p&l", "pnl", "profit", "gain"])
        chg_col  = _find_col(cols, ["charges", "tax", "stt", "brokerage"])

        if sym_col and bdate and savg:
            out = pd.DataFrame()
            out["symbol"]       = df[sym_col].astype(str).str.strip()
            out["trade_date"]   = pd.to_datetime(df[bdate],  errors="coerce", dayfirst=True).dt.date
            out["exit_date"]    = pd.to_datetime(df[sdate],  errors="coerce", dayfirst=True).dt.date if sdate else out["trade_date"]
            out["quantity"]     = pd.to_numeric(df[bqty],    errors="coerce").fillna(0).astype(int) if bqty else 0
            out["buy_price"]    = pd.to_numeric(df[bavg],    errors="coerce").fillna(0)
            out["sell_price"]   = pd.to_numeric(df[savg],    errors="coerce").fillna(0)
            out["gross_pnl"]    = pd.to_numeric(df[pnl_col], errors="coerce").fillna(0) if pnl_col else (out["sell_price"] - out["buy_price"]) * out["quantity"]
            out["charges"]      = pd.to_numeric(df[chg_col], errors="coerce").fillna(0) if chg_col else 0
            out["net_pnl"]      = out["gross_pnl"] - out["charges"]
            out["position_type"] = np.where(out["gross_pnl"] >= 0, "CALL", "PUT")  # proxy
            out = out.dropna(subset=["trade_date", "symbol"])
            return out

    # ── Schema B: Console Trade Book (individual buy/sell legs) ──────────
    if any(c for c in cols if "trade_type" in c or "trade type" in c):
        sym_col  = _find_col(cols, ["symbol", "trading_symbol", "tradingsymbol"])
        date_col = _find_col(cols, ["trade_date", "date"])
        type_col = _find_col(cols, ["trade_type", "trade type", "type"])
        qty_col  = _find_col(cols, ["quantity", "qty", "trade_qty"])
        px_col   = _find_col(cols, ["price", "traded_price", "avg_price"])

        if sym_col and date_col and px_col:
            df["_date"]  = pd.to_datetime(df[date_col], errors="coerce", dayfirst=True).dt.date
            df["_qty"]   = pd.to_numeric(df[qty_col], errors="coerce").fillna(0)
            df["_px"]    = pd.to_numeric(df[px_col],  errors="coerce").fillna(0)
            df["_type"]  = df[type_col].astype(str).str.upper()

            buys  = df[df["_type"].str.contains("BUY",  na=False)].copy()
            sells = df[df["_type"].str.contains("SELL", na=False)].copy()

            # Merge buy + sell on symbol + date (simple same-day pairing)
            buys  = buys.rename(columns={"_px": "buy_price",  "_qty": "buy_qty"})
            sells = sells.rename(columns={"_px": "sell_price", "_qty": "sell_qty"})
            merged = pd.merge(
                buys[  [sym_col, "_date", "buy_price",  "buy_qty"]],
                sells[ [sym_col, "_date", "sell_price", "sell_qty"]],
                on=[sym_col, "_date"], how="inner",
            )
            merged["quantity"]  = merged[["buy_qty", "sell_qty"]].min(axis=1).astype(int)
            merged["gross_pnl"] = (merged["sell_price"] - merged["buy_price"]) * merged["quantity"]
            merged["charges"]   = 0.0
            merged["net_pnl"]   = merged["gross_pnl"]
            merged["trade_date"] = merged["_date"]
            merged["exit_date"]  = merged["_date"]
            merged["symbol"]     = merged[sym_col]
            merged["position_type"] = "CALL"
            return merged[["symbol", "trade_date", "exit_date", "quantity",
                            "buy_price", "sell_price", "gross_pnl", "charges",
                            "net_pnl", "position_type"]]

    return None


def _find_col(cols: list, candidates: list) -> str | None:
    """Return the first column name that contains any of the candidate substrings."""
    for cand in candidates:
        for col in cols:
            if cand.lower() in col.lower():
                return col
    return None


def _insert_imported_trades(df, uid: int) -> int:
    """Insert normalised DataFrame rows into the Trade table as IMPORT trades."""
    db = SessionLocal()
    inserted = 0
    try:
        for _, row in df.iterrows():
            try:
                trade_date = row.get("trade_date")
                if trade_date is None:
                    continue
                if hasattr(trade_date, "date"):
                    trade_date = trade_date.date()

                exit_date = row.get("exit_date") or trade_date
                if hasattr(exit_date, "date"):
                    exit_date = exit_date.date()

                gross = float(row.get("gross_pnl") or 0)
                chg   = float(row.get("charges")   or 0)
                net   = float(row.get("net_pnl")    or gross - chg)

                entry_dt = datetime.datetime.combine(trade_date, datetime.time(9, 15))
                exit_dt  = datetime.datetime.combine(exit_date,  datetime.time(15, 30))

                t = Trade(
                    user_id       = uid,
                    date          = trade_date,
                    trade_mode    = "IMPORT",
                    symbol        = str(row.get("symbol", "UNKNOWN"))[:50],
                    position_type = str(row.get("position_type", "CALL")),
                    entry_time    = entry_dt,
                    exit_time     = exit_dt,
                    entry_prem    = float(row.get("buy_price")  or 0),
                    exit_prem     = float(row.get("sell_price") or 0),
                    strike        = None,
                    quantity      = int(row.get("quantity") or 0),
                    gross_pnl     = gross,
                    charges       = chg,
                    net_pnl       = net,
                    exit_reason   = "Console Import",
                    or_high       = None,
                    or_low        = None,
                )
                db.add(t)
                inserted += 1
            except Exception:
                continue   # skip malformed rows

        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    return inserted


# ══════════════════════════════════════════════════════════════════════════════
# KITE TRADE SYNC  — fetch + save real executed trades from your Kite account
# ══════════════════════════════════════════════════════════════════════════════

def _get_broker_for_user(uid: int):
    """Return the KiteBroker for *uid* from the engine pool, or None."""
    try:
        from core.engine_pool import engine_pool
        ue = engine_pool.get(uid)
        return ue.broker if ue else None
    except Exception:
        return None


def _parse_option_symbol(tradingsymbol: str) -> dict:
    """
    Extract strike and option type from a Kite NFO tradingsymbol.

    Kite formats:
      NIFTY24MAY24000CE   (weekly)
      NIFTY2451524000CE   (weekly, YYMMDD)
      NIFTY24MAY2400PE    (older monthly)
    Returns {"strike": int, "option_type": "CALL"|"PUT", "underlying": str}
    """
    ts = tradingsymbol.upper()
    m  = re.search(r'(\d{3,6})(CE|PE)$', ts)
    if m:
        strike     = int(m.group(1))
        opt_type   = "CALL" if m.group(2) == "CE" else "PUT"
        underlying = re.match(r'([A-Z]+)', ts).group(1) if ts else "NIFTY"
        return {"strike": strike, "option_type": opt_type, "underlying": underlying}
    return {"strike": None, "option_type": "CALL", "underlying": ts}


def _compute_charges_for_trade(buy_val: float, sell_val: float,
                                qty: int, cfg) -> float:
    """
    Estimate total charges for one round-trip options trade using the same
    formula as TradingEngine._handle_signal().
    """
    turnover   = buy_val + sell_val
    brokerage  = cfg.brokerage_per_order * 2
    stt        = sell_val * cfg.stt_pct
    exch       = turnover * cfg.exchange_charges_pct
    gst        = (brokerage + exch) * cfg.gst_pct
    sebi       = turnover * cfg.sebi_charges_pct
    stamp      = buy_val  * cfg.stamp_duty_pct
    return round(brokerage + stt + exch + gst + sebi + stamp, 2)


def _match_kite_trades(raw_trades: list, cfg) -> list:
    """
    Given a flat list of Kite trade dicts (BUY + SELL fills for today),
    group by tradingsymbol and match BUY/SELL legs into completed round-trips.

    Returns a list of matched-trade dicts ready for saving or display.
    """
    # Accumulate fills per symbol
    by_sym: dict[str, dict] = defaultdict(lambda: {
        "buy_qty": 0, "sell_qty": 0,
        "buy_val": 0.0, "sell_val": 0.0,
        "buy_times": [], "sell_times": [],
        "product": "MIS", "exchange": "NFO",
    })

    for t in raw_trades:
        sym    = t.get("tradingsymbol", "")
        qty    = int(t.get("quantity", 0))
        price  = float(t.get("average_price", 0) or 0)
        ttype  = (t.get("transaction_type") or "").upper()
        ts_raw = t.get("fill_timestamp") or t.get("exchange_timestamp") or ""

        # Parse timestamp
        fill_dt = None
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
            try:
                fill_dt = datetime.datetime.strptime(str(ts_raw)[:19], fmt)
                break
            except ValueError:
                pass

        s = by_sym[sym]
        s["product"]  = t.get("product",  "MIS")
        s["exchange"] = t.get("exchange", "NFO")

        if ttype == "BUY":
            s["buy_qty"] += qty
            s["buy_val"] += price * qty
            if fill_dt:
                s["buy_times"].append(fill_dt)
        elif ttype == "SELL":
            s["sell_qty"] += qty
            s["sell_val"] += price * qty
            if fill_dt:
                s["sell_times"].append(fill_dt)

    today   = datetime.date.today()
    results = []

    for sym, s in by_sym.items():
        matched_qty = min(s["buy_qty"], s["sell_qty"])
        if matched_qty == 0:
            continue   # open position — skip

        buy_avg  = round(s["buy_val"]  / s["buy_qty"],  2) if s["buy_qty"]  else 0.0
        sell_avg = round(s["sell_val"] / s["sell_qty"], 2) if s["sell_qty"] else 0.0

        buy_val  = buy_avg  * matched_qty
        sell_val = sell_avg * matched_qty
        gross    = round((sell_avg - buy_avg) * matched_qty, 2)
        charges  = _compute_charges_for_trade(buy_val, sell_val, matched_qty, cfg)
        net      = round(gross - charges, 2)

        entry_time = min(s["buy_times"])  if s["buy_times"]  else None
        exit_time  = min(s["sell_times"]) if s["sell_times"] else None

        opt_info = _parse_option_symbol(sym)

        results.append({
            "tradingsymbol": sym,
            "underlying":    opt_info["underlying"],
            "strike":        opt_info["strike"],
            "option_type":   opt_info["option_type"],
            "quantity":      matched_qty,
            "buy_avg":       buy_avg,
            "sell_avg":      sell_avg,
            "gross_pnl":     gross,
            "charges":       charges,
            "net_pnl":       net,
            "entry_time":    entry_time.isoformat() if entry_time else None,
            "exit_time":     exit_time.isoformat()  if exit_time  else None,
            "date":          today.isoformat(),
            "product":       s["product"],
            "exchange":      s["exchange"],
            # open leg info (non-zero if partial fill)
            "open_buy_qty":  s["buy_qty"]  - matched_qty,
            "open_sell_qty": s["sell_qty"] - matched_qty,
        })

    results.sort(key=lambda x: x["net_pnl"], reverse=True)
    return results


@analytics_bp.route("/api/kite/trades/today")
@jwt_required()
def kite_trades_today():
    """
    Fetch today's executed trades from the user's Kite account, match BUY+SELL
    legs, and return the computed P&L for each symbol.

    Does NOT save anything — call /api/kite/trades/sync to persist.
    """
    uid    = _uid()
    broker = _get_broker_for_user(uid)

    if not broker:
        return _bad("No active Kite session — log in via Kite first.", 409)

    try:
        raw = broker.kite.trades()
    except Exception as e:
        logger.warning(f"kite.trades() failed for user {uid}: {e}")
        return _bad(f"Kite API error: {e}", 502)

    if not raw:
        return jsonify({
            "ok":     True,
            "count":  0,
            "trades": [],
            "raw_fills": 0,
            "message": "No trades executed today on your Kite account.",
        })

    matched = _match_kite_trades(raw, broker.config)

    # Compute daily summary
    total_net    = round(sum(t["net_pnl"]   for t in matched), 2)
    total_gross  = round(sum(t["gross_pnl"] for t in matched), 2)
    total_chg    = round(sum(t["charges"]   for t in matched), 2)
    wins         = [t for t in matched if t["net_pnl"] > 0]

    return jsonify({
        "ok":         True,
        "raw_fills":  len(raw),
        "count":      len(matched),
        "trades":     matched,
        "summary": {
            "total_net_pnl":   total_net,
            "total_gross_pnl": total_gross,
            "total_charges":   total_chg,
            "win_rate":        round(len(wins) / len(matched) * 100, 1) if matched else 0,
        },
    })


@analytics_bp.route("/api/kite/trades/sync", methods=["POST"])
@jwt_required()
def kite_trades_sync():
    """
    Fetch today's Kite trades, match legs, and persist matched trades to the
    DB as LIVE-mode Trade records.

    Idempotent: if a LIVE trade already exists for (user, date, symbol) today,
    it is skipped (no duplicate). Use ?overwrite=1 to replace existing records.
    """
    uid       = _uid()
    overwrite = request.args.get("overwrite", "0") == "1"
    broker    = _get_broker_for_user(uid)

    if not broker:
        return _bad("No active Kite session — log in via Kite first.", 409)

    try:
        raw = broker.kite.trades()
    except Exception as e:
        return _bad(f"Kite API error: {e}", 502)

    if not raw:
        return jsonify({"ok": True, "synced": 0, "skipped": 0,
                        "message": "No trades to sync — no fills today."})

    matched = _match_kite_trades(raw, broker.config)
    today   = datetime.date.today()
    db      = SessionLocal()
    synced = skipped = 0

    try:
        for t in matched:
            sym = t["tradingsymbol"]

            # Check for existing record
            existing = (
                db.query(Trade)
                .filter_by(user_id=uid, trade_mode="LIVE", date=today, symbol=sym)
                .first()
            )

            if existing and not overwrite:
                skipped += 1
                continue

            if existing and overwrite:
                db.delete(existing)
                db.flush()

            entry_dt = (datetime.datetime.fromisoformat(t["entry_time"])
                        if t["entry_time"] else None)
            exit_dt  = (datetime.datetime.fromisoformat(t["exit_time"])
                        if t["exit_time"]  else None)

            db.add(Trade(
                user_id       = uid,
                date          = today,
                trade_mode    = "LIVE",
                symbol        = sym,
                position_type = t["option_type"],
                entry_time    = entry_dt,
                exit_time     = exit_dt,
                entry_prem    = t["buy_avg"],
                exit_prem     = t["sell_avg"],
                strike        = t["strike"],
                quantity      = t["quantity"],
                gross_pnl     = t["gross_pnl"],
                charges       = t["charges"],
                net_pnl       = t["net_pnl"],
                exit_reason   = "Kite Sync",
                or_high       = None,
                or_low        = None,
            ))
            synced += 1

        db.commit()
        total_net = round(sum(t["net_pnl"] for t in matched), 2)
        return jsonify({
            "ok":       True,
            "synced":   synced,
            "skipped":  skipped,
            "total_net_pnl": total_net,
            "message":  f"Synced {synced} trade(s) from Kite"
                        + (f" — {skipped} already existed (use ?overwrite=1 to replace)" if skipped else ""),
        })
    except Exception as e:
        db.rollback()
        logger.exception("kite_trades_sync failed")
        return _bad(str(e), 500)
    finally:
        db.close()
