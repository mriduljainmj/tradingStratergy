"""
Screener — sector-wise NSE stock browser with watchlist management.

Endpoints
---------
GET  /screener                          → screener page
GET  /api/screener/sectors              → list of sector names + stock counts
GET  /api/screener/quotes?sector=<s>   → live quotes for all stocks in sector
GET  /api/screener/technicals?symbol=X → MA20/50/200, RSI14, 52W H/L for 1 stock
GET  /api/screener/watchlist            → user's watchlist (with live quotes)
POST /api/screener/watchlist            → add symbol to watchlist
DELETE /api/screener/watchlist/<symbol> → remove symbol from watchlist
"""

import datetime
import logging

from flask import Blueprint, jsonify, render_template, request
from flask_jwt_extended import get_jwt_identity, jwt_required

from dashboard.nse_data import SECTORS, STOCK_INFO, get_name_for_symbol, get_sector_for_symbol
from db.database import SessionLocal
from db.models import Watchlist

logger = logging.getLogger(__name__)
screener_bp = Blueprint("screener", __name__)

# Module-level broker ref injected from app.py
_broker = None


def register_screener_broker(broker):
    global _broker
    _broker = broker


# ── helpers ───────────────────────────────────────────────────────────────────

def _uid() -> int:
    return int(get_jwt_identity())


def _bad(msg: str, code: int = 400):
    return jsonify({"ok": False, "error": msg}), code


def _kite_symbols(symbols: list[str]) -> list[str]:
    """Convert bare NSE symbols to 'NSE:SYMBOL' format for Kite quote API."""
    return [f"NSE:{s}" for s in symbols]


def _fetch_quotes(symbols: list[str]) -> dict:
    """
    Fetch live quotes for a list of NSE symbols.
    Returns dict keyed by bare symbol (without 'NSE:' prefix).
    Each value: {ltp, open, high, low, prev_close, change_pct, volume}
    """
    if not _broker or not symbols:
        return {}

    kite_keys = _kite_symbols(symbols)
    # Kite allows up to 500 symbols per call; chunk if needed
    results = {}
    for i in range(0, len(kite_keys), 500):
        chunk = kite_keys[i:i + 500]
        try:
            raw = _broker.kite.quote(chunk)
        except Exception as e:
            logger.warning(f"Quote fetch failed: {e}")
            continue
        for key, q in raw.items():
            sym = key.replace("NSE:", "")
            ohlc = q.get("ohlc", {})
            prev = ohlc.get("close", 0) or 0
            ltp  = q.get("last_price", 0) or 0
            chg  = ((ltp - prev) / prev * 100) if prev else 0
            results[sym] = {
                "ltp":        round(ltp, 2),
                "open":       round(ohlc.get("open",  0), 2),
                "high":       round(ohlc.get("high",  0), 2),
                "low":        round(ohlc.get("low",   0), 2),
                "prev_close": round(prev, 2),
                "change_pct": round(chg, 2),
                "volume":     q.get("volume", 0),
            }
    return results


def _compute_rsi(closes: list[float], period: int = 14) -> float | None:
    """Wilder's RSI on a list of closing prices. Returns None if insufficient data."""
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - 100 / (1 + rs), 2)


def _compute_technicals(symbol: str) -> dict:
    """
    Fetch ~200 days of daily history for `symbol` and compute:
    MA20, MA50, MA200, RSI14, 52W High/Low, distance from extremes.

    Strategy: look up the NSE instrument token from the cached instruments
    list, then call Kite historical_data().  The instruments list is fetched
    once per day by the broker's existing NFO cache; for NSE equities we
    fetch it here (may be a fresh network call).
    """
    if not _broker:
        return {"error": "Broker not available"}

    today   = datetime.date.today()
    from_dt = today - datetime.timedelta(days=300)   # ~200 trading days

    try:
        # Resolve NSE instrument token (single network call, ~100 KB JSON)
        instruments = _broker.kite.instruments("NSE")
        token = None
        for inst in instruments:
            if inst.get("tradingsymbol") == symbol and inst.get("segment") == "NSE":
                token = inst["instrument_token"]
                break
        if not token:
            return {"error": f"Instrument '{symbol}' not found on NSE"}

        records = _broker.kite.historical_data(
            token,
            f"{from_dt} 09:15:00",
            f"{today} 15:30:00",
            "day",
        )
    except Exception as e:
        return {"error": str(e)}

    if not records:
        return {"error": "No historical data"}

    closes = [r["close"] for r in records]
    highs  = [r["high"]  for r in records]
    lows   = [r["low"]   for r in records]

    def ma(n):
        if len(closes) < n:
            return None
        return round(sum(closes[-n:]) / n, 2)

    w52_high = round(max(highs),  2)
    w52_low  = round(min(lows),   2)
    ltp      = closes[-1]

    return {
        "ma20":     ma(20),
        "ma50":     ma(50),
        "ma200":    ma(200),
        "rsi14":    _compute_rsi(closes, 14),
        "w52_high": w52_high,
        "w52_low":  w52_low,
        "ltp":      round(ltp, 2),
        "dist_from_52h": round((ltp - w52_high) / w52_high * 100, 2) if w52_high else None,
        "dist_from_52l": round((ltp - w52_low)  / w52_low  * 100, 2) if w52_low  else None,
        "bars":     len(closes),
    }


# ── Page ──────────────────────────────────────────────────────────────────────

@screener_bp.route("/screener")
def screener_page():
    return render_template("screener.html")


# ── API ───────────────────────────────────────────────────────────────────────

@screener_bp.route("/api/screener/sectors")
def list_sectors():
    """Return sector names with stock counts."""
    data = []
    for name, syms in SECTORS.items():
        data.append({"sector": name, "count": len(syms)})
    return jsonify({"ok": True, "sectors": data})


@screener_bp.route("/api/screener/quotes")
def sector_quotes():
    """
    Live quotes for all stocks in the requested sector.
    Query param: sector (name from SECTORS dict)
    """
    sector = request.args.get("sector", "").strip()
    if not sector or sector not in SECTORS:
        # If no valid sector, return all symbols
        symbols = list(STOCK_INFO.keys())
    else:
        symbols = SECTORS[sector]

    if not _broker:
        # Return empty quote placeholders so UI can still show the stock list
        rows = [
            {
                "symbol":  s,
                "name":    get_name_for_symbol(s),
                "sector":  get_sector_for_symbol(s),
                "ltp":     None, "open": None, "high": None,
                "low":     None, "prev_close": None,
                "change_pct": None, "volume": None,
            }
            for s in symbols
        ]
        return jsonify({"ok": True, "data": rows, "live": False})

    quotes = _fetch_quotes(symbols)
    rows = []
    for sym in symbols:
        q = quotes.get(sym, {})
        rows.append({
            "symbol":     sym,
            "name":       get_name_for_symbol(sym),
            "sector":     get_sector_for_symbol(sym),
            "ltp":        q.get("ltp"),
            "open":       q.get("open"),
            "high":       q.get("high"),
            "low":        q.get("low"),
            "prev_close": q.get("prev_close"),
            "change_pct": q.get("change_pct"),
            "volume":     q.get("volume"),
        })
    return jsonify({"ok": True, "data": rows, "live": bool(quotes)})


@screener_bp.route("/api/screener/technicals")
@jwt_required()
def stock_technicals():
    """Compute MA20/50/200, RSI14, 52W H/L for a single stock. Slow — call on demand."""
    symbol = request.args.get("symbol", "").strip().upper()
    if not symbol:
        return _bad("symbol is required")
    result = _compute_technicals(symbol)
    return jsonify({"ok": "error" not in result, "symbol": symbol, **result})


# ── Watchlist ─────────────────────────────────────────────────────────────────

@screener_bp.route("/api/screener/watchlist", methods=["GET"])
@jwt_required()
def get_watchlist():
    uid = _uid()
    db  = SessionLocal()
    try:
        items = db.query(Watchlist).filter_by(user_id=uid).order_by(Watchlist.added_at).all()
        symbols = [w.symbol for w in items]
        quotes  = _fetch_quotes(symbols) if _broker and symbols else {}

        data = []
        for w in items:
            q = quotes.get(w.symbol, {})
            entry = w.to_dict()
            entry.update({
                "ltp":        q.get("ltp"),
                "open":       q.get("open"),
                "high":       q.get("high"),
                "low":        q.get("low"),
                "prev_close": q.get("prev_close"),
                "change_pct": q.get("change_pct"),
                "volume":     q.get("volume"),
            })
            data.append(entry)
        return jsonify({"ok": True, "data": data})
    finally:
        db.close()


@screener_bp.route("/api/screener/watchlist", methods=["POST"])
@jwt_required()
def add_to_watchlist():
    uid  = _uid()
    body = request.get_json(silent=True) or {}
    sym  = (body.get("symbol") or "").strip().upper()
    if not sym:
        return _bad("symbol is required")
    name   = get_name_for_symbol(sym) or body.get("name", sym)
    sector = get_sector_for_symbol(sym) or body.get("sector", "")

    db = SessionLocal()
    try:
        existing = db.query(Watchlist).filter_by(user_id=uid, symbol=sym).first()
        if existing:
            return jsonify({"ok": True, "msg": "Already in watchlist"})
        db.add(Watchlist(user_id=uid, symbol=sym, company_name=name, sector=sector))
        db.commit()
        return jsonify({"ok": True, "symbol": sym})
    except Exception as e:
        db.rollback()
        logger.error(f"Watchlist add failed: {e}")
        return _bad(str(e), 500)
    finally:
        db.close()


@screener_bp.route("/api/screener/watchlist/<symbol>", methods=["DELETE"])
@jwt_required()
def remove_from_watchlist(symbol: str):
    uid = _uid()
    sym = symbol.strip().upper()
    db  = SessionLocal()
    try:
        row = db.query(Watchlist).filter_by(user_id=uid, symbol=sym).first()
        if row:
            db.delete(row)
            db.commit()
        return jsonify({"ok": True, "symbol": sym})
    except Exception as e:
        db.rollback()
        return _bad(str(e), 500)
    finally:
        db.close()
