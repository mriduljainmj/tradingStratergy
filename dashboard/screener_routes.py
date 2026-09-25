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

from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required

from dashboard.nse_data import SECTORS, STOCK_INFO, get_name_for_symbol, get_sector_for_symbol
from db.database import SessionLocal
from db.models import Watchlist

logger = logging.getLogger(__name__)
screener_bp = Blueprint("screener", __name__)


# ── helpers ───────────────────────────────────────────────────────────────────

def _uid() -> int:
    return int(get_jwt_identity())


def _bad(msg: str, code: int = 400):
    return jsonify({"ok": False, "error": msg}), code


def _get_broker():
    """Return the requesting user's KiteBroker, or None if not authenticated."""
    try:
        from flask_jwt_extended import get_jwt_identity, verify_jwt_in_request
        verify_jwt_in_request(optional=True)
        uid_str = get_jwt_identity()
        if uid_str is None:
            return None
        from core.engine_pool import engine_pool
        ue = engine_pool.get(int(uid_str))
        return ue.broker if ue else None
    except Exception:
        return None


def _kite_symbols(symbols: list) -> list:
    """Convert bare NSE symbols to 'NSE:SYMBOL' format for Kite quote API."""
    return [f"NSE:{s}" for s in symbols]


def _fetch_quotes(symbols: list, broker=None) -> dict:
    """
    Fetch live quotes for a list of NSE symbols.
    Returns dict keyed by bare symbol (without 'NSE:' prefix).
    Each value: {ltp, open, high, low, prev_close, change_pct, volume}
    """
    if broker is None:
        broker = _get_broker()
    if not broker or not symbols:
        return {}

    kite_keys = _kite_symbols(symbols)
    # Kite allows up to 500 symbols per call; chunk if needed
    results = {}
    for i in range(0, len(kite_keys), 500):
        chunk = kite_keys[i:i + 500]
        try:
            raw = broker.kite.quote(chunk)
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


def _compute_technicals(symbol: str, broker=None) -> dict:
    """
    Fetch ~200 days of daily history for `symbol` and compute:
    MA20, MA50, MA200, RSI14, 52W High/Low, distance from extremes.

    Strategy: look up the NSE instrument token from the cached instruments
    list, then call Kite historical_data().  The instruments list is fetched
    once per day by the broker's existing NFO cache; for NSE equities we
    fetch it here (may be a fresh network call).
    """
    if broker is None:
        broker = _get_broker()
    if not broker:
        return {"error": "Broker not available"}

    today   = datetime.date.today()
    from_dt = today - datetime.timedelta(days=300)   # ~200 trading days

    try:
        # Resolve NSE instrument token (single network call, ~100 KB JSON)
        instruments = broker.kite.instruments("NSE")
        token = None
        for inst in instruments:
            if inst.get("tradingsymbol") == symbol and inst.get("segment") == "NSE":
                token = inst["instrument_token"]
                break
        if not token:
            return {"error": f"Instrument '{symbol}' not found on NSE"}

        records = broker.kite.historical_data(
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


# ── API ───────────────────────────────────────────────────────────────────────

@screener_bp.route("/api/screener/sectors")
def list_sectors():
    """Return sector names with stock counts."""
    data = []
    for name, syms in SECTORS.items():
        data.append({"sector": name, "count": len(syms)})
    return jsonify({"ok": True, "sectors": data})


@screener_bp.route("/api/screener/all-instruments")
@jwt_required()
def all_instruments():
    """
    Return ALL NSE equity instruments from Kite's instrument cache.
    Enriches with sector/name from STOCK_INFO where available.
    Returns: [{symbol, name, sector}] — metadata only, no live prices.
    """
    from dashboard.routes import _nse_inst_cache
    import datetime as _dt

    broker = _get_broker()
    cache  = _nse_inst_cache

    # Refresh instruments cache if stale
    if broker:
        today_s = str(_dt.date.today())
        if cache.get("date") != today_s or not cache.get("data"):
            try:
                cache["data"] = broker.kite.instruments("NSE")
                cache["date"] = today_s
            except Exception as e:
                logger.warning(f"Instruments fetch failed: {e}")

    instruments = cache.get("data", [])

    # Build result — main NSE board EQ only (exclude SME, bonds, ETF NAVs, debentures)
    seen = set()
    result = []
    for inst in instruments:
        if inst.get("instrument_type") != "EQ":
            continue
        if inst.get("segment") != "NSE":  # excludes NSE-SME, bonds, NCDs
            continue
        sym = inst.get("tradingsymbol", "").strip()
        if not sym or sym in seen:
            continue
        # Skip bond/NCD symbols starting with a digit
        if sym[0].isdigit():
            continue
        # Skip ETF iNAV indicator values (e.g. GROWSLINAV, SETFGOINAV)
        if sym.endswith('INAV') or sym.endswith('NAV'):
            continue
        # Skip SME board, trade-to-trade, bonds with hyphens (e.g. DEEM-SM, AAFS27B-N3)
        if '-' in sym:
            continue
        seen.add(sym)
        info = STOCK_INFO.get(sym, {})
        result.append({
            "symbol": sym,
            "name":   info.get("name") or inst.get("name", sym),
            "sector": info.get("sector", ""),
        })

    # Also add our curated list stocks that might not be in EQ instruments (indices etc.)
    for sym, info in STOCK_INFO.items():
        if sym not in seen:
            result.append({"symbol": sym, "name": info.get("name", sym), "sector": info.get("sector", "")})
            seen.add(sym)

    result.sort(key=lambda x: x["symbol"])
    return jsonify({"ok": True, "data": result, "total": len(result)})


@screener_bp.route("/api/screener/batch-quotes")
@jwt_required()
def batch_quotes():
    """
    Fetch live quotes for an explicit list of symbols.
    Query param: symbols=SYM1,SYM2,...  (comma-separated, max 500)
    """
    raw     = request.args.get("symbols", "")
    symbols = [s.strip().upper() for s in raw.split(",") if s.strip()][:500]
    if not symbols:
        return jsonify({"ok": False, "error": "symbols param required"}), 400

    broker = _get_broker()
    if not broker:
        return jsonify({"ok": True, "data": {}, "live": False})

    quotes = _fetch_quotes(symbols, broker)
    return jsonify({"ok": True, "data": quotes, "live": True})


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

    broker = _get_broker()
    if not broker:
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

    quotes = _fetch_quotes(symbols, broker)
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
    broker = _get_broker()
    result = _compute_technicals(symbol, broker)
    return jsonify({"ok": "error" not in result, "symbol": symbol, **result})


# ── Watchlist ─────────────────────────────────────────────────────────────────

# ── Momentum framework ────────────────────────────────────────────────────────
# Per-day cache: symbol → momentum metrics (avoids re-fetching daily candles)
_mom_cache: dict = {"date": None, "data": {}}
_mcap_cache: dict = {"date": None, "data": {}}   # symbol -> market cap (₹ Cr)


def _market_cap_cr(symbol: str, ltp: float = 0.0):
    """Market cap in ₹ crore via yfinance fast_info (cached per day). None on failure.
    The June-batch notes screen for 10B–1T INR (₹1,000 Cr – ₹1 lakh Cr) — this
    keeps momentum in mid/large caps and excludes mega-caps that move too slowly."""
    today = str(datetime.date.today())
    if _mcap_cache["date"] != today:
        _mcap_cache["date"] = today
        _mcap_cache["data"] = {}
    if symbol in _mcap_cache["data"]:
        return _mcap_cache["data"][symbol]
    val = None
    try:
        import yfinance as yf
        fi = yf.Ticker(symbol + ".NS").fast_info
        mc = None
        try:
            mc = fi["marketCap"]
        except Exception:
            mc = None
        if not mc:
            try:
                sh = fi["shares"]
                if sh and ltp:
                    mc = sh * ltp
            except Exception:
                mc = None
        if mc:
            val = round(mc / 1e7)   # rupees -> crore
    except Exception as e:
        logger.debug(f"market cap fetch failed for {symbol}: {e}")
    _mcap_cache["data"][symbol] = val
    return val


def _momentum_row(sym: str, recs: list, mcap):
    """Score one stock against the June-batch momentum checklist.
    recs: Kite daily records (needs ~250+ for full accuracy).
    mcap: market cap in ₹ crore, or None if unknown.
    Returns the result row dict, or None if not enough data."""

    def _ema(vals, period):
        if len(vals) < period:
            return None
        k = 2 / (period + 1)
        e = sum(vals[:period]) / period
        for v in vals[period:]:
            e = v * k + e * (1 - k)
        return e

    def _rsi(vals, period=14):
        if len(vals) < period + 1:
            return None
        g = l = 0.0
        for i in range(1, period + 1):
            d = vals[i] - vals[i - 1]
            g += max(d, 0); l += max(-d, 0)
        ag, al = g / period, l / period
        for i in range(period + 1, len(vals)):
            d = vals[i] - vals[i - 1]
            ag = (ag * (period - 1) + max(d, 0)) / period
            al = (al * (period - 1) + max(-d, 0)) / period
        if al == 0:
            return 100.0
        return 100 - 100 / (1 + ag / al)

    closes = [r["close"] for r in recs]
    highs  = [r["high"] for r in recs]
    lows   = [r["low"] for r in recs]
    vols   = [r.get("volume", 0) or 0 for r in recs]
    if len(closes) < 60:
        return None

    ltp   = closes[-1]
    e10, e21, e50, e200 = (_ema(closes, 10), _ema(closes, 21),
                           _ema(closes, 50), _ema(closes, 200))
    # 200-EMA slope: compare to its value ~20 sessions ago
    e200_prev = _ema(closes[:-20], 200) if len(closes) >= 220 else None
    w52_high = max(highs[-250:]) if len(highs) >= 20 else max(highs)
    w52_low  = min(lows[-250:])  if len(lows)  >= 20 else min(lows)
    vol_avg50 = sum(vols[-50:]) / min(len(vols), 50)
    last_vol  = vols[-1]
    rsi = _rsi(closes)
    ret_1m = (ltp - closes[-21]) / closes[-21] * 100 if len(closes) >= 21 else None
    ret_3m = (ltp - closes[-63]) / closes[-63] * 100 if len(closes) >= 63 else ret_1m
    dist_52h = (ltp - w52_high) / w52_high * 100 if w52_high else 0
    above_52l = (ltp - w52_low) / w52_low * 100 if w52_low else 0
    vol_ratio = (last_vol / vol_avg50) if vol_avg50 else 0

    # ── Criteria from the momentum notes (Weinstein stage-2 + buy checklist) ─
    def C(label, ok, value, target, weight):
        return {"label": label, "pass": bool(ok), "value": value,
                "target": target, "weight": weight}

    mcap_ok = (mcap is not None and 1000 <= mcap <= 100000)
    mcap_val = ("n/a" if mcap is None else
                f"₹{mcap/1000:.1f}k Cr" if mcap >= 1000 else f"₹{mcap:.0f} Cr")
    # None = unknown (excluded from score); True/False otherwise
    mcap_pass = None if mcap is None else mcap_ok
    checks = [
        {"label": "Market cap ₹1k–1L Cr", "pass": mcap_pass, "value": mcap_val,
         "target": "10B–1T INR (mid/large cap)", "weight": 14},
        C("Above 200 EMA (Stage 2)",  e200 and ltp > e200,
          f"₹{ltp:.0f} vs {e200:.0f}" if e200 else "n/a", "price > 200EMA", 15),
        C("200 EMA rising",           e200 and e200_prev and e200 > e200_prev,
          "rising" if (e200 and e200_prev and e200 > e200_prev) else "flat/down",
          "20-day slope up", 15),
        C("Above 50 EMA (mid-term)",  e50 and ltp > e50,
          f"₹{ltp:.0f} vs {e50:.0f}" if e50 else "n/a", "price > 50EMA", 10),
        C("50 EMA > 200 EMA (golden)", e50 and e200 and e50 > e200,
          "aligned" if (e50 and e200 and e50 > e200) else "not aligned",
          "50EMA > 200EMA", 10),
        C("Short-term up (10>21 EMA)", e10 and e21 and e10 > e21,
          "up" if (e10 and e21 and e10 > e21) else "down",
          "10EMA > 21EMA", 8),
        C("Near 52-wk high",          dist_52h >= -5,
          f"{dist_52h:+.1f}%", "within 5% of high", 15),
        C("Well above 52-wk low",     above_52l >= 25,
          f"+{above_52l:.0f}%", "≥ 25% above low", 7),
        C("Breakout volume",          vol_ratio >= 1.5,
          f"{vol_ratio:.1f}x", "≥ 1.5x 50-day avg", 10),
        C("Relative strength (3M)",   (ret_3m or 0) > 0,
          f"{ret_3m:+.1f}%" if ret_3m is not None else "n/a", "3-month return > 0", 6),
        C("RSI healthy (55-80)",      rsi is not None and 55 <= rsi <= 80,
          f"{rsi:.0f}" if rsi is not None else "n/a", "55 – 80", 4),
    ]
    scored = [c for c in checks if c["pass"] is not None]
    got    = sum(c["weight"] for c in scored if c["pass"])
    total  = sum(c["weight"] for c in scored) or 1
    score  = round(got / total * 100)
    grade = ("A" if score >= 80 else "B" if score >= 60 else
             "C" if score >= 40 else "D")
    verdict = ("Strong momentum" if score >= 80 else
               "Building momentum" if score >= 60 else
               "Weak / early" if score >= 40 else "Avoid")
    row = {
        "symbol": sym, "ltp": round(ltp, 2), "score": score, "grade": grade,
        "verdict": verdict, "passed": sum(1 for c in checks if c["pass"]),
        "total_checks": len(scored), "market_cap_cr": mcap,
        "ret_1m": round(ret_1m, 1) if ret_1m is not None else None,
        "checks": checks,
    }
    return row


# ── Full-universe momentum scan (two-stage, background) ──────────────────────
# Stage 1: bulk pre-screen via yfinance fast_info — market cap in the notes'
#          10B–1T INR band, within 5% of 52-wk high, above the 200-DMA.
#          Stocks failing market cap are EXCLUDED (hard filter).
# Stage 2: full 11-check June-batch scoring via Kite daily history.
_scan_state: dict = {"date": None, "status": "idle", "stage": "", "done": 0,
                     "total": 0, "qualifiers": 0, "results": [], "error": ""}
_scan_lock = __import__("threading").Lock()


def _universe(cache) -> list:
    """[(symbol, token)] for the tradable NSE main-board universe."""
    out, seen = [], set()
    for inst in (cache.get("data") or []):
        if inst.get("instrument_type") != "EQ" or inst.get("segment") != "NSE":
            continue
        sym = (inst.get("tradingsymbol") or "").strip()
        if (not sym or sym in seen or sym[0].isdigit()
                or sym.endswith("NAV") or "-" in sym):
            continue
        seen.add(sym)
        out.append((sym, inst.get("instrument_token")))
    return out


def _run_momentum_scan(broker):
    import time as _t
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from dashboard.routes import _nse_inst_cache

    st = _scan_state
    try:
        universe = _universe(_nse_inst_cache)
        if not universe:
            st.update(status="error", error="Instrument list not loaded — open the Stocks tab once, then retry.")
            return
        st.update(status="running", stage="Stage 1 · bulk screen",
                  done=0, total=len(universe), qualifiers=0, results=[], error="")

        def stage1(pair):
            sym, token = pair
            try:
                import yfinance as yf
                fi = yf.Ticker(sym + ".NS").fast_info
                mc = fi["marketCap"]; price = fi["lastPrice"]
                yh = fi["yearHigh"]; dma200 = fi["twoHundredDayAverage"]
                if not (mc and price and yh and dma200):
                    return None
                mcap_cr = mc / 1e7
                if not (1000 <= mcap_cr <= 100000):
                    return None            # hard market-cap exclusion
                if price < 0.95 * yh:      # within 5% of the 52-week high
                    return None
                if price <= dma200:        # must be above long-term trend
                    return None
                return (sym, token, round(mcap_cr))
            except Exception:
                return None

        survivors = []
        with ThreadPoolExecutor(max_workers=8) as ex:
            futs = [ex.submit(stage1, p) for p in universe]
            for f in as_completed(futs):
                st["done"] += 1
                r = f.result()
                if r:
                    survivors.append(r)
                    st["qualifiers"] = len(survivors)

        survivors = survivors[:250]   # sanity cap for stage 2
        st.update(stage="Stage 2 · checklist scoring", done=0, total=len(survivors))

        to_dt   = datetime.date.today()
        from_dt = to_dt - datetime.timedelta(days=420)
        results = []
        for sym, token, mcap_cr in survivors:
            st["done"] += 1
            if not token:
                continue
            try:
                recs = broker.kite.historical_data(
                    token, f"{from_dt} 09:15:00", f"{to_dt} 15:30:00", "day")
                _t.sleep(0.12)
            except Exception as e:
                logger.debug(f"scan stage2 {sym}: {e}")
                continue
            row = _momentum_row(sym, recs, mcap_cr)
            if row is None:
                continue
            row["name"] = get_name_for_symbol(sym) or sym
            row["sector"] = get_sector_for_symbol(sym) or ""
            results.append(row)
            st["results"] = sorted(results, key=lambda r: r["score"], reverse=True)

        st.update(status="done", stage="complete",
                  results=sorted(results, key=lambda r: r["score"], reverse=True))
        logger.info(f"Momentum scan complete: {len(results)} qualifiers "
                    f"from {len(universe)} stocks.")
    except Exception as e:
        logger.exception("momentum scan crashed")
        st.update(status="error", error=str(e)[:200])


@screener_bp.route("/api/screener/momentum-scan", methods=["POST"])
@jwt_required()
def start_momentum_scan():
    """Kick off (or return) today's full-universe momentum scan."""
    import threading as _th
    broker = _get_broker()
    if not broker:
        return _bad("Broker not available — log in to Kite first.", 409)
    today = str(datetime.date.today())
    with _scan_lock:
        if _scan_state["status"] == "running":
            return jsonify({"ok": True, "status": "running"})
        if _scan_state["date"] == today and _scan_state["status"] == "done" \
                and not (request.get_json(silent=True) or {}).get("force"):
            return jsonify({"ok": True, "status": "done", "cached": True})
        _scan_state["date"] = today
        _scan_state["status"] = "running"
    _th.Thread(target=_run_momentum_scan, args=(broker,), daemon=True,
               name="MomentumScan").start()
    return jsonify({"ok": True, "status": "started"})


@screener_bp.route("/api/screener/momentum-scan")
@jwt_required()
def momentum_scan_status():
    st = _scan_state
    return jsonify({"ok": True, "status": st["status"], "stage": st["stage"],
                    "done": st["done"], "total": st["total"],
                    "qualifiers": st["qualifiers"], "error": st["error"],
                    "results": st["results"]})


@screener_bp.route("/api/screener/momentum")
@jwt_required()
def momentum_rank():
    """
    Rank symbols by price momentum. Symbols come from ?symbols=A,B,C or the
    user's watchlist (?list= optional). Capped at 60 symbols per call to stay
    inside Kite rate limits; results cached for the day.

    Score = 0.6 × 1-month return + 0.4 × 1-week return, with trend flags.
    """
    uid    = _uid()
    broker = _get_broker()
    if not broker:
        return _bad("Broker not available — log in to Kite first.", 409)

    syms_arg = (request.args.get("symbols") or "").strip()
    if syms_arg:
        symbols = [s.strip().upper() for s in syms_arg.split(",") if s.strip()][:60]
    else:
        wl_list = (request.args.get("list") or "").strip()
        db = SessionLocal()
        try:
            q = db.query(Watchlist).filter_by(user_id=uid)
            if wl_list:
                q = q.filter(Watchlist.list_name == wl_list)
            symbols = [w.symbol for w in q.all()][:60]
        finally:
            db.close()
    if not symbols:
        return jsonify({"ok": True, "data": [], "msg": "No symbols — star some stocks first."})

    import time as _t
    today_s = str(datetime.date.today())
    if _mom_cache["date"] != today_s:
        _mom_cache["date"] = today_s
        _mom_cache["data"] = {}
    cache = _mom_cache["data"]

    # Resolve tokens once from the cached NSE instrument list
    from dashboard.routes import _nse_inst_cache
    tok_map = {i.get("tradingsymbol"): i.get("instrument_token")
               for i in (_nse_inst_cache.get("data") or [])
               if i.get("segment") == "NSE"}

    to_dt   = datetime.date.today()
    from_dt = to_dt - datetime.timedelta(days=420)   # ~1yr trading + EMA200 warmup

    out = []
    for sym in symbols:
        if sym in cache:
            out.append(cache[sym]); continue
        token = tok_map.get(sym)
        if not token:
            continue
        try:
            recs = broker.kite.historical_data(
                token, f"{from_dt} 09:15:00", f"{to_dt} 15:30:00", "day")
            _t.sleep(0.15)
        except Exception as e:
            logger.warning(f"momentum fetch failed for {sym}: {e}")
            continue
        mcap = _market_cap_cr(sym, recs[-1]["close"] if recs else 0)
        row = _momentum_row(sym, recs, mcap)
        if row is None:
            continue
        cache[sym] = row
        out.append(row)

    out.sort(key=lambda r: r["score"], reverse=True)
    return jsonify({"ok": True, "data": out, "count": len(out),
                    "framework": "Weinstein Stage-2 momentum (June batch checklist)"})


@screener_bp.route("/api/screener/watchlist", methods=["GET"])
@jwt_required()
def get_watchlist():
    uid = _uid()
    wl_list = (request.args.get("list") or "").strip()
    db  = SessionLocal()
    try:
        q = db.query(Watchlist).filter_by(user_id=uid)
        if wl_list:
            q = q.filter(Watchlist.list_name == wl_list)
        items = q.order_by(Watchlist.added_at).all()
        symbols = [w.symbol for w in items]
        broker  = _get_broker()
        quotes  = _fetch_quotes(symbols, broker) if broker and symbols else {}

        all_lists = sorted({(r[0] or "My Watchlist") for r in
                            db.query(Watchlist.list_name).filter_by(user_id=uid).all()}) or ["My Watchlist"]

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
        return jsonify({"ok": True, "data": data, "lists": all_lists})
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
    name    = get_name_for_symbol(sym) or body.get("name", sym)
    sector  = get_sector_for_symbol(sym) or body.get("sector", "")
    wl_list = (body.get("list") or "My Watchlist").strip()[:100] or "My Watchlist"

    db = SessionLocal()
    try:
        existing = db.query(Watchlist).filter_by(user_id=uid, symbol=sym).first()
        if existing:
            # Symbol is unique per user — treat re-add as a move between lists
            if existing.list_name != wl_list:
                existing.list_name = wl_list
                db.commit()
                return jsonify({"ok": True, "symbol": sym, "moved_to": wl_list})
            return jsonify({"ok": True, "msg": "Already in watchlist"})
        db.add(Watchlist(user_id=uid, symbol=sym, company_name=name,
                         sector=sector, list_name=wl_list))
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
