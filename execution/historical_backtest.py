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

    def run_day(self, date: datetime.date, direction: str = "BOTH") -> dict:
        state    = BotState(app_mode="BACKTEST")
        state.trade_direction = direction.upper()   # respect CALL / PUT / BOTH filter
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
            logger.error(f"Backtest: Kite API error for {date}: {e}")
            return {"error": str(e), "date": str(date)}

        logger.info(f"Backtest: fetched {len(records)} 1m candles for NIFTY on {date}")

        if not records:
            # Kite returns an empty list for market holidays.
            # It can also return empty for ~10–20 minutes right after market close
            # while the day's data is being finalised — try again shortly if so.
            return {
                "error": (
                    f"No data returned for {date}. "
                    "This is either a market holiday or the data is not yet "
                    "finalised (Kite publishes intraday data ~15 min after 15:30). "
                    "Please try again shortly."
                ),
                "date": str(date),
            }

        def to_candles(recs):
            return [
                {
                    "time":  int(r["date"].timestamp()),
                    "open":  r["open"],  "high": r["high"],
                    "low":   r["low"],   "close": r["close"],
                }
                for r in recs
            ]

        # ── Walk back 5 trading days for historical chart context ────────────
        context_from = date
        prev_count   = 0
        _d           = date - datetime.timedelta(days=1)
        while prev_count < 5:
            if OptionsMath.is_trading_day(_d):
                context_from = _d
                prev_count  += 1
            _d -= datetime.timedelta(days=1)

        # ── 1M candles: full historical window (prev days + today) ───────────
        # The frontend uses rawNiftyCandles = candles_1m when it exists, and
        # aggregateCandles() converts them to any timeframe (5M, 15M, …).
        # Fetching the full context as 1M data lets every timeframe tab show
        # the same multi-day history without a separate 5M request.
        try:
            hist_records = self.broker.get_historical_data(
                self.config.index_token,
                f"{context_from} 09:15:00",
                f"{date} 15:30:00",
                "minute",
            )
            candles_1m = to_candles(hist_records)
        except Exception:
            candles_1m = to_candles(records)   # fallback: today only

        # ── 5M candles: kept as a lightweight fallback / day/week views ──────
        try:
            chart_records = self.broker.get_historical_data(
                self.config.index_token,
                f"{context_from} 09:15:00",
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
                    # Use actual DTE stored by _look_for_entry (same fix as strategy.py)
                    if strategy._expiry_date is not None:
                        _IST_tz   = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
                        tick_date = datetime.datetime.fromtimestamp(int(dt.timestamp()), tz=_IST_tz).date()
                        dte       = max((strategy._expiry_date - tick_date).days, 0.5)
                        T         = dte / 365.25
                    else:
                        T = 4 / 365.25
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
            "direction":          direction.upper(),
            "candles":            candles,
            "candles_1m":         candles_1m,
            "markers":            state.markers,
            "option_markers":     state.option_markers,
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
            "exit_reason":        state.exit_reason,
        }

    # ── Patch option prices + P&L with real Kite NFO data ─────────────────────

    def _patch_real_option_data(self, state: BotState, strategy: ORBStrategy,
                                date: datetime.date):
        """
        Fetch real 1-min OHLC for the traded options contract from Kite and
        replace the Black-Scholes estimates in `state`.  Falls back silently
        if Kite has no data for that date/contract.
        """
        suffix     = "CE" if state.position_type == "CALL" else "PE"
        min_expiry = OptionsMath.get_expiry_date(date)
        records, _contract = self.broker.get_option_history(
            strategy.strike, suffix, date, min_expiry=min_expiry
        )

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
            """Find the NFO candle closest to unix_ts.
            First tries exact minute and ±10 min offsets; falls back to
            absolute-closest candle in the whole day's data."""
            base = (unix_ts // 60) * 60
            for offset in range(0, 601, 60):   # 0, 60, 120 … 600 s (10 min)
                for sign in (1, -1) if offset else (1,):
                    c = nfo.get(base + sign * offset)
                    if c:
                        return c
            # Last-resort: pick the candle with the smallest time-distance
            if not nfo:
                return None
            closest_ts = min(nfo, key=lambda k: abs(k - base))
            logger.debug(
                f"nearest_candle fallback: target {base} → closest {closest_ts} "
                f"(delta {abs(closest_ts - base)}s)"
            )
            return nfo[closest_ts]

        # ── Markers tell us entry and exit unix timestamps ─────────────────────
        markers = state.markers
        entry_unix = markers[0]["time"] if markers else None
        exit_unix  = markers[1]["time"] if len(markers) > 1 else None

        # ── Update entry premium ───────────────────────────────────────────────
        entry_candle = nearest_candle(entry_unix) if entry_unix else None
        logger.info(
            f"{date}: entry_unix={entry_unix}, "
            f"nfo_keys_sample={sorted(nfo)[:3] if nfo else '[]'}, "
            f"entry_candle={'found @' + str(entry_candle['time']) if entry_candle else 'NOT FOUND'}"
        )
        if entry_candle:
            real_entry = entry_candle["close"]
            bs_entry   = state.entry_prem          # keep for logging
            state.entry_prem          = round(real_entry, 2)
            strategy.state.entry_prem = round(real_entry, 2)
            strategy.target_prem      = real_entry + self.config.target_pts
            state.target_prem         = round(strategy.target_prem, 2)
            # Patch BUY markers: NIFTY chart → NIFTY price, options chart → real fill price
            pos_type = state.position_type
            if markers and state.entry_nifty_px:
                markers[0]["text"] = f"BUY {pos_type} @ ₹{state.entry_nifty_px:.0f}"
            if state.option_markers:
                state.option_markers[0]["text"] = f"BUY {pos_type} @ ₹{real_entry:.0f}"
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

            # Patch EXIT markers: NIFTY chart → NIFTY price (from Phase 1), options → real premium
            _epatch = {
                "position": "belowBar" if net_pnl > 0 else "aboveBar",
                "color":    "#089981"  if net_pnl > 0 else "#F23645",
                "shape":    "arrowUp"  if net_pnl > 0 else "arrowDown",
            }
            if len(state.markers) > 1:
                # Use NIFTY exit price stored during Phase 1 strategy replay
                nifty_exit_px = state.exit_nifty_px or exit_candle["close"]
                state.markers[-1].update({**_epatch, "text": f"EXIT @ ₹{nifty_exit_px:.0f}"})
            if len(state.option_markers) > 1:
                state.option_markers[-1].update({**_epatch, "text": f"EXIT @ ₹{real_exit:.0f}"})

            logger.info(
                f"{date}: Real exit premium ₹{real_exit:.2f} | "
                f"Real Net P&L ₹{net_pnl:.2f}"
            )

        # ── Replace option_prices with real NFO OHLC candles ──────────────────
        # Also prepend the previous 3 trading days so the option chart shows
        # historical context just like the NIFTY chart does.
        prev_candles: list = []
        _prev_d      = date - datetime.timedelta(days=1)
        _prev_fetched = 0
        while _prev_fetched < 3:
            if OptionsMath.is_trading_day(_prev_d):
                try:
                    prev_recs, _ = self.broker.get_option_history(
                        strategy.strike, suffix, _prev_d, min_expiry=min_expiry
                    )
                    for r in (prev_recs or []):
                        ts = (int(r["date"].timestamp()) // 60) * 60
                        prev_candles.append({
                            "time":  ts,
                            "open":  r["open"],  "high": r["high"],
                            "low":   r["low"],   "close": r["close"],
                        })
                except Exception as _e:
                    logger.debug(f"Prev-day option fetch skipped for {_prev_d}: {_e}")
                _prev_fetched += 1
            _prev_d -= datetime.timedelta(days=1)

        today_candles = sorted(nfo.values(), key=lambda c: c["time"])
        all_candles   = sorted(prev_candles + today_candles, key=lambda c: c["time"])
        if all_candles:
            state.option_prices = all_candles

    # ── Grid-search optimizer ──────────────────────────────────────────────────

    def optimize(
        self,
        from_date: datetime.date,
        to_date:   datetime.date,
        or_times:  list,          # e.g. ["09:20", "09:25", "09:30", "09:35"]
        targets:   list,          # e.g. [80, 100, 120, 140, 160]
        directions: list,         # subset of ["CALL", "BOTH", "PUT"]
        metric:    str = "total_pnl",
    ) -> dict:
        """
        Grid-search over every (direction × target_pts × or_end_time) combination.

        NIFTY 1-min data is fetched ONCE per trading day and reused across all
        combinations.  No real-option patching is performed — Black-Scholes
        prices are used throughout so the search runs in seconds, not minutes.
        Results are sorted by `metric` (total_pnl | win_rate | trade_days).
        """
        import copy

        delta = (to_date - from_date).days
        if delta > MAX_RANGE_DAYS:
            return {"error": f"Range exceeds {MAX_RANGE_DAYS} days."}
        if from_date > to_date:
            return {"error": "from_date must be before to_date."}

        combos = [
            (d, int(t), o)
            for d in directions
            for t in targets
            for o in or_times
        ]
        if not combos:
            return {"error": "No combinations to test."}
        if len(combos) > 500:
            return {"error": f"Too many combinations ({len(combos)}), max 500."}

        # ── Pre-fetch NIFTY 1M data for each trading day (done ONCE) ─────────
        trading_days = []
        cur = from_date
        while cur <= to_date:
            if cur.weekday() < 5:
                trading_days.append(cur)
            cur += datetime.timedelta(days=1)

        logger.info(
            f"Optimizer: pre-fetching {len(trading_days)} days for "
            f"{len(combos)} combos …"
        )
        day_records: dict = {}   # date → list[Kite record]
        for day in trading_days:
            try:
                recs = self.broker.get_historical_data(
                    self.config.index_token,
                    f"{day} 09:15:00",
                    f"{day} 15:30:00",
                    "minute",
                )
                if recs:
                    day_records[day] = recs
            except Exception as e:
                logger.warning(f"Optimizer: skipping {day}: {e}")

        if not day_records:
            return {"error": "Could not fetch NIFTY data for the date range."}

        logger.info(
            f"Optimizer: fetched {len(day_records)} days. "
            f"Replaying {len(combos)} strategy combos …"
        )

        # ── Replay strategy for every combo using cached records ──────────────
        results = []
        for direction, target_pts, or_time_str in combos:
            cfg = copy.copy(self.config)
            cfg.target_pts = target_pts
            try:
                parts = or_time_str.split(":")
                cfg.or_end_time = datetime.time(int(parts[0]), int(parts[1]))
            except Exception:
                pass

            day_pnls   = []
            wins       = 0
            losses     = 0
            trade_days = 0

            for day, records in day_records.items():
                state    = BotState(app_mode="BACKTEST")
                state.trade_direction = direction.upper()
                strategy = ORBStrategy(cfg, state)

                exited = False
                for r in records:
                    dt = r["date"]
                    if not exited:
                        sig = strategy.process_tick(
                            int(dt.timestamp()), dt.time(),
                            r["open"], r["high"], r["low"], r["close"],
                        )
                        if sig and sig["action"] == "SELL":
                            exited = True

                pnl = state.net_pnl
                day_pnls.append(pnl)
                if state.markers:       # a trade was taken
                    trade_days += 1
                    if pnl > 0:
                        wins += 1
                    else:
                        losses += 1

            total_pnl = round(sum(day_pnls), 2)
            win_rate  = round(wins / trade_days * 100, 1) if trade_days else 0.0
            # Simple profit-factor: gross_win / gross_loss (avoid div-by-zero)
            gross_win  = sum(p for p in day_pnls if p > 0)
            gross_loss = abs(sum(p for p in day_pnls if p < 0))
            profit_factor = round(gross_win / gross_loss, 2) if gross_loss else (
                float("inf") if gross_win > 0 else 0.0
            )

            results.append({
                "direction":     direction,
                "or_end_time":   or_time_str,
                "target_pts":    target_pts,
                "total_pnl":     total_pnl,
                "win_rate":      win_rate,
                "profit_factor": profit_factor,
                "trade_days":    trade_days,
                "total_days":    len(day_records),
                "wins":          wins,
                "losses":        losses,
            })

        # Sort
        valid = {"total_pnl", "win_rate", "profit_factor", "trade_days"}
        sort_key = metric if metric in valid else "total_pnl"
        results.sort(key=lambda x: (
            x[sort_key] if x[sort_key] != float("inf") else 1e9
        ), reverse=True)

        logger.info(
            f"Optimizer: done. Best {sort_key} = "
            f"{results[0][sort_key] if results else 'n/a'}"
        )
        return {
            "results":      results,
            "total_combos": len(combos),
            "days_tested":  len(day_records),
            "from_date":    str(from_date),
            "to_date":      str(to_date),
        }

    # ── Range backtest ─────────────────────────────────────────────────────────

    def run_range(self, from_date: datetime.date, to_date: datetime.date,
                  direction: str = "BOTH") -> dict:
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
                result = self.run_day(current, direction=direction)
                if "error" not in result:
                    daily.append(result)
            current += datetime.timedelta(days=1)

        if not daily:
            return {"error": "No valid trading days in the selected range."}

        # ── Market-trend classification (5-day vs 20-day SMA on daily closes) ──
        # Fetch 20 extra calendar days before from_date so the first SMA values
        # are meaningful even at the start of the requested range.
        trend_map: dict[str, dict] = {}
        try:
            trend_from = from_date - datetime.timedelta(days=30)
            nifty_daily = self.broker.get_historical_data(
                self.config.index_token,
                f"{trend_from} 09:15:00",
                f"{to_date} 15:30:00",
                "day",
            )
            if nifty_daily:
                # Build a list of (date, open, high, low, close) in order
                day_rows = []
                for r in nifty_daily:
                    d = r["date"].date() if hasattr(r["date"], "date") else r["date"]
                    day_rows.append((d, r["open"], r["close"]))

                for i, (d, open_, close) in enumerate(day_rows):
                    closes = [c for _, _, c in day_rows[:i + 1]]

                    sma5  = sum(closes[-5:])  / min(len(closes), 5)
                    sma20 = sum(closes[-20:]) / min(len(closes), 20)

                    # Day-over-day change %
                    prev_close = day_rows[i - 1][2] if i > 0 else open_
                    chg_pct = round((close - prev_close) / prev_close * 100, 2)

                    # Trend: price above both SMAs and 5>20 = up, below both = down
                    if close > sma5 and sma5 > sma20:
                        trend = "Uptrend"
                    elif close < sma5 and sma5 < sma20:
                        trend = "Downtrend"
                    else:
                        trend = "Sideways"

                    trend_map[str(d)] = {
                        "trend":    trend,
                        "sma5":     round(sma5,  2),
                        "sma20":    round(sma20, 2),
                        "close":    round(close, 2),
                        "chg_pct":  chg_pct,
                    }
        except Exception as _te:
            logger.warning(f"Trend analysis skipped: {_te}")

        # ── Stamp trend onto each day's result ───────────────────────────────
        for d in daily:
            ti = trend_map.get(d["date"], {})
            d["market_trend"] = ti.get("trend", "Unknown")
            d["nifty_chg_pct"] = ti.get("chg_pct", None)

        traded = [d for d in daily if d["trade_taken"]]
        wins   = [d for d in traded if d["pnl"] > 0]
        real_count = sum(1 for d in traded if d.get("used_real_options"))

        # ── Performance by trend ─────────────────────────────────────────────
        perf_by_trend: dict[str, dict] = {}
        for label in ("Uptrend", "Downtrend", "Sideways"):
            days_in  = [d for d in traded if d.get("market_trend") == label]
            wins_in  = [d for d in days_in if d["pnl"] > 0]
            pnl_in   = round(sum(d["pnl"] for d in days_in), 2)
            perf_by_trend[label] = {
                "trades":   len(days_in),
                "wins":     len(wins_in),
                "losses":   len(days_in) - len(wins_in),
                "win_rate": round(len(wins_in) / len(days_in) * 100, 1) if days_in else 0,
                "total_pnl": pnl_in,
                "avg_pnl":  round(pnl_in / len(days_in), 2) if days_in else 0,
            }

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
                "market_trend":   d.get("market_trend", "Unknown"),
                "nifty_chg_pct":  d.get("nifty_chg_pct"),
            })

        return {
            "from_date":       str(from_date),
            "to_date":         str(to_date),
            "direction":       direction.upper(),
            "total_days":      len(daily),
            "trade_days":      len(traded),
            "wins":            len(wins),
            "losses":          len(traded) - len(wins),
            "win_rate":        round(len(wins) / len(traded) * 100, 1) if traded else 0,
            "total_pnl":       round(sum(d["pnl"] for d in daily), 2),
            "real_options_used": real_count,
            "bs_fallback":     len(traded) - real_count,
            "cumulative":      cumulative,
            "perf_by_trend":   perf_by_trend,
        }
