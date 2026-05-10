import datetime
import logging

from config.settings import TradingConfig
from core.options_math import OptionsMath
from core.state import BotState
from core.strategy import ORBStrategy
from execution.broker import KiteBroker

logger = logging.getLogger(__name__)

MAX_RANGE_DAYS = 60


class HistoricalBacktester:
    def __init__(self, config: TradingConfig, broker: KiteBroker):
        self.config = config
        self.broker = broker

    # ── Single-day backtest ────────────────────────────────────────────────────

    def run_day(self, date: datetime.date) -> dict:
        state    = BotState(app_mode="BACKTEST")
        strategy = ORBStrategy(self.config, state)

        # ── Fetch NIFTY 1m candles ────────────────────────────────────────────
        try:
            records = self.broker.get_historical_data(
                self.config.index_token,
                f"{date} 09:15:00",
                f"{date} 15:30:00",
                "minute",
            )
        except Exception as e:
            logger.error(f"Failed to fetch 1-min data for {date}: {e}")
            return {"error": str(e), "date": str(date)}

        if not records:
            return {"error": "No data returned (market holiday?)", "date": str(date)}

        def to_candles(recs):
            return [
                {
                    "time":  int(r["date"].timestamp()),
                    "open":  r["open"],  "high": r["high"],
                    "low":   r["low"],   "close": r["close"],
                }
                for r in recs
            ]

        candles_1m = to_candles(records)

        try:
            chart_records = self.broker.get_historical_data(
                self.config.index_token,
                f"{date} 09:15:00",
                f"{date} 15:30:00",
                "5minute",
            )
            candles = to_candles(chart_records)
        except Exception:
            candles = []

        # ── Phase 1: NIFTY replay → find OR + entry (Black-Scholes prices) ────
        # After exit we keep looping to append BS option prices for the rest of
        # the day so the options chart shows the full session, not just the trade.
        exited = False
        for r in records:
            dt = r["date"]

            if not exited:
                signal = strategy.process_tick(
                    int(dt.timestamp()), dt.time(),
                    r["open"], r["high"], r["low"], r["close"],
                )
                if signal and signal["action"] == "SELL":
                    exited = True
            else:
                # Post-exit: compute BS price for this candle and extend chart
                if strategy.strike is not None:
                    T   = 4 / 365.25
                    cfg = self.config
                    is_call = state.position_type == "CALL"
                    bs  = OptionsMath.bs_call if is_call else OptionsMath.bs_put
                    ts  = int(dt.timestamp())
                    if is_call:
                        op = bs(r["open"],  strategy.strike, T, cfg.risk_free_rate, cfg.assumed_iv)
                        hp = bs(r["high"],  strategy.strike, T, cfg.risk_free_rate, cfg.assumed_iv)
                        lp = bs(r["low"],   strategy.strike, T, cfg.risk_free_rate, cfg.assumed_iv)
                        cp = bs(r["close"], strategy.strike, T, cfg.risk_free_rate, cfg.assumed_iv)
                    else:
                        # For puts: high NIFTY → low option price, so swap high/low
                        op = bs(r["open"],  strategy.strike, T, cfg.risk_free_rate, cfg.assumed_iv)
                        hp = bs(r["low"],   strategy.strike, T, cfg.risk_free_rate, cfg.assumed_iv)
                        lp = bs(r["high"],  strategy.strike, T, cfg.risk_free_rate, cfg.assumed_iv)
                        cp = bs(r["close"], strategy.strike, T, cfg.risk_free_rate, cfg.assumed_iv)
                    state.option_prices.append({
                        "time":  ts,
                        "open":  round(op, 2), "high": round(hp, 2),
                        "low":   round(lp, 2), "close": round(cp, 2),
                    })

        # ── Phase 2: Replace BS option data with real NFO candles if available ─
        if state.position_type != "NONE" and strategy.strike:
            self._patch_real_option_data(state, strategy, date)
        else:
            logger.info(f"{date}: no trade triggered — skipping NFO fetch")

        trade_taken = bool(state.markers)
        return {
            "date":               str(date),
            "candles":            candles,
            "candles_1m":         candles_1m,
            "markers":            state.markers,
            "entry_prem":         state.entry_prem,
            "exit_prem":          state.exit_prem,
            "gross_pnl":          state.gross_pnl,
            "total_charges":      state.total_charges,
            "net_pnl":            state.net_pnl,
            "pnl":                state.net_pnl,
            "brokerage_breakdown": state.brokerage_breakdown,
            "position_type":      state.position_type,
            "or_high":            state.or_high,
            "or_low":             state.or_low,
            "current_high":       state.current_high,
            "current_low":        state.current_low,
            "option_prices":      state.option_prices,
            "option_label":       state.option_label,
            "option_expiry":      state.option_expiry,
            "target_prem":        state.target_prem,
            "logs":               state.logs,
            "trade_taken":        trade_taken,
            "used_real_options":  state.used_real_options,
            "strike":             strategy.strike if hasattr(strategy, 'strike') else None,
        }

    # ── Patch option prices + P&L with real Kite NFO data ─────────────────────

    def _patch_real_option_data(self, state: BotState, strategy: ORBStrategy,
                                date: datetime.date):
        """
        Fetch real 1-min OHLC for the traded options contract from Kite and
        replace the Black-Scholes estimates in `state`.  Falls back silently
        if Kite has no data for that date/contract.
        """
        suffix  = "CE" if state.position_type == "CALL" else "PE"
        records = self.broker.get_option_history(strategy.strike, suffix, date)

        if not records:
            logger.info(
                f"{date}: No real NFO data for NIFTY{strategy.strike}{suffix} "
                f"— keeping Black-Scholes estimates"
            )
            state.used_real_options = False
            return

        state.used_real_options = True

        # Build a lookup: minute-boundary unix_ts → candle dict
        nfo: dict[int, dict] = {}
        for r in records:
            ts = int(r["date"].timestamp())
            ts_min = (ts // 60) * 60          # floor to minute boundary
            nfo[ts_min] = {
                "time":  ts_min,
                "open":  r["open"],  "high": r["high"],
                "low":   r["low"],   "close": r["close"],
            }

        def nearest_candle(unix_ts: int) -> dict | None:
            """Find the NFO candle closest to unix_ts (tries ±0, ±60, ±120 s)."""
            base = (unix_ts // 60) * 60
            for offset in (0, 60, -60, 120, -120, 180, -180):
                c = nfo.get(base + offset)
                if c:
                    return c
            return None

        # ── Markers tell us entry and exit unix timestamps ─────────────────────
        markers = state.markers
        entry_unix = markers[0]["time"] if markers else None
        exit_unix  = markers[1]["time"] if len(markers) > 1 else None

        # ── Update entry premium ───────────────────────────────────────────────
        entry_candle = nearest_candle(entry_unix) if entry_unix else None
        if entry_candle:
            real_entry = entry_candle["close"]
            bs_entry   = state.entry_prem          # keep for logging
            state.entry_prem          = round(real_entry, 2)
            strategy.state.entry_prem = round(real_entry, 2)
            strategy.target_prem      = real_entry + self.config.target_pts
            state.target_prem         = round(strategy.target_prem, 2)
            # Patch the BUY marker text so it shows the real fill price
            if markers:
                pos_type = state.position_type
                markers[0]["text"] = f"BUY {pos_type} @ ₹{real_entry:.0f}"
            logger.info(
                f"{date}: Real entry premium ₹{real_entry:.2f} "
                f"(was ₹{bs_entry:.2f} BS estimate)"
            )

        # ── Update exit premium + recalculate P&L ─────────────────────────────
        exit_candle = nearest_candle(exit_unix) if exit_unix else None
        if exit_candle and entry_candle:
            real_exit = exit_candle["close"]
            cfg       = self.config

            gross_pnl = (real_exit - state.entry_prem) * cfg.qty
            buy_val   = state.entry_prem * cfg.qty
            sell_val  = real_exit        * cfg.qty
            turnover  = buy_val + sell_val

            brokerage = cfg.brokerage_per_order * 2
            stt       = sell_val * cfg.stt_pct
            exch      = turnover * cfg.exchange_charges_pct
            gst       = (brokerage + exch) * cfg.gst_pct
            sebi      = turnover * cfg.sebi_charges_pct
            stamp     = buy_val  * cfg.stamp_duty_pct
            total_ch  = round(brokerage + stt + exch + gst + sebi + stamp, 2)
            net_pnl   = round(gross_pnl - total_ch, 2)

            state.exit_prem        = round(real_exit, 2)
            state.gross_pnl        = round(gross_pnl, 2)
            state.total_charges    = total_ch
            state.net_pnl          = net_pnl
            state.pnl              = net_pnl

            state.brokerage_breakdown = {
                "Brokerage (₹20/order)":       round(brokerage, 2),
                "STT (0.0625% on sell)":        round(stt,       2),
                "Exchange (0.053%)":            round(exch,      2),
                "GST (18% on Brk+Exc)":         round(gst,       2),
                "SEBI (₹10/Cr)":                round(sebi,      2),
                "Stamp Duty (0.003% on buy)":   round(stamp,     2),
            }

            # Update exit marker to reflect real P&L
            if len(state.markers) > 1:
                state.markers[-1].update({
                    "position": "belowBar" if net_pnl > 0 else "aboveBar",
                    "color":    "#089981"  if net_pnl > 0 else "#F23645",
                    "shape":    "arrowUp"  if net_pnl > 0 else "arrowDown",
                    "text":     f"EXIT: ₹{net_pnl:.0f}",
                })

            logger.info(
                f"{date}: Real exit premium ₹{real_exit:.2f} | "
                f"Real Net P&L ₹{net_pnl:.2f}"
            )

        # ── Replace option_prices with real NFO OHLC candles ──────────────────
        # Send the full trading session so the options chart shows the entire day.
        all_candles = sorted(nfo.values(), key=lambda c: c["time"])
        if all_candles:
            state.option_prices = all_candles

    # ── Range backtest ─────────────────────────────────────────────────────────

    def run_range(self, from_date: datetime.date, to_date: datetime.date) -> dict:
        delta = (to_date - from_date).days
        if delta > MAX_RANGE_DAYS:
            return {"error": f"Range exceeds {MAX_RANGE_DAYS} days. "
                             "Please select a shorter window."}
        if from_date > to_date:
            return {"error": "from_date must be before to_date."}

        daily   = []
        current = from_date
        while current <= to_date:
            if current.weekday() < 5:          # skip weekends
                result = self.run_day(current)
                if "error" not in result:
                    daily.append(result)
            current += datetime.timedelta(days=1)

        if not daily:
            return {"error": "No valid trading days in the selected range."}

        traded = [d for d in daily if d["trade_taken"]]
        wins   = [d for d in traded if d["pnl"] > 0]
        real_count = sum(1 for d in traded if d.get("used_real_options"))

        running    = 0
        cumulative = []
        for d in daily:
            running += d["pnl"]
            cumulative.append({
                "date":           d["date"],
                "pnl":            round(d["pnl"], 2),
                "cumulative":     round(running, 2),
                "position_type":  d.get("position_type", "NONE"),
                "entry_prem":     round(d.get("entry_prem") or 0, 2),
                "exit_prem":      round(d.get("exit_prem")  or 0, 2),
                "trade_taken":    d.get("trade_taken", False),
                "used_real_options": d.get("used_real_options", False),
            })

        return {
            "from_date":   str(from_date),
            "to_date":     str(to_date),
            "total_days":  len(daily),
            "trade_days":  len(traded),
            "wins":        len(wins),
            "losses":      len(traded) - len(wins),
            "win_rate":    round(len(wins) / len(traded) * 100, 1) if traded else 0,
            "total_pnl":   round(sum(d["pnl"] for d in daily), 2),
            "real_options_used": real_count,
            "bs_fallback":       len(traded) - real_count,
            "cumulative":  cumulative,
        }
