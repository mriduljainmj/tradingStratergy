import datetime
import logging
import threading
import time

from config.settings import TradingConfig
from core.state import BotState
from core.strategy import ORBStrategy
from execution.broker import KiteBroker

logger = logging.getLogger(__name__)

# Always use IST regardless of the server's local timezone (Render runs UTC)
_IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

def _now() -> datetime.datetime:
    """Current datetime in IST."""
    return datetime.datetime.now(tz=_IST)


class TradingEngine:
    """Orchestrates data fetching, strategy execution, and order routing."""

    def __init__(self, config: TradingConfig, state: BotState, broker: KiteBroker,
                 user_id: int = None):
        self.config  = config
        self.state   = state
        self.broker  = broker
        self.user_id = user_id   # set for analytics DB saves
        self.strategy = ORBStrategy(config, state)
        self._stop_event = threading.Event()

        # Previous trading day candles — fetched once and prepended to today's
        # data so the chart shows the prior session for context (like TradingView).
        self._prev_nifty_5m: list = []
        self._prev_nifty_1m: list = []
        self._prev_nifty_day: datetime.date | None = None

    def stop(self):
        self._stop_event.set()

    def _trade_already_completed_today(self, real_money: bool) -> bool:
        """Return True if a completed trade for today is already saved in the DB.

        Used as a pre-flight guard in run_live() to prevent:
        1. Double-entry when the engine auto-restarts after a trade finishes.
        2. False "Target Hit" exits during _backfill_session() which uses
           Black-Scholes pricing (no real option LTPs) and can over-estimate
           the option premium, triggering targets that never actually traded.
        """
        if not self.user_id:
            return False
        try:
            from db.database import SessionLocal
            from db.models import Trade
            db = SessionLocal()
            try:
                today     = _now().date()
                mode_str  = "LIVE" if real_money else "PAPER"
                trade = (
                    db.query(Trade)
                    .filter(
                        Trade.user_id    == self.user_id,
                        Trade.date       == today,
                        Trade.trade_mode == mode_str,
                    )
                    .first()
                )
                return trade is not None
            finally:
                db.close()
        except Exception as e:
            logger.warning(f"_trade_already_completed_today check failed: {e}")
            return False   # safe default: allow the run if we can't check

    def _save_trade(self, trade_mode: str):
        """Persist the just-completed trade to the analytics DB."""
        if not self.user_id:
            return
        from db.helpers import save_completed_trade
        st  = self.state
        cfg = self.config
        # Derive entry/exit datetimes from marker unix timestamps
        entry_ts = st.markers[0]["time"]  if len(st.markers) > 0 else None
        exit_ts  = st.markers[-1]["time"] if len(st.markers) > 1 else None
        to_dt = lambda ts: datetime.datetime.fromtimestamp(ts, tz=_IST).replace(tzinfo=None) if ts else None
        # Use the entry marker's date so the record isn't misdated if the
        # server clock crosses midnight IST between entry and save.
        trade_date = (datetime.datetime.fromtimestamp(entry_ts, tz=_IST).date()
                      if entry_ts else _now().date())
        save_completed_trade(
            user_id       = self.user_id,
            trade_mode    = trade_mode,
            date          = trade_date,
            position_type = st.position_type,
            entry_prem    = st.entry_prem,
            exit_prem     = st.exit_prem,
            strike        = self.strategy.strike,
            quantity      = cfg.qty,
            gross_pnl     = st.gross_pnl,
            charges       = st.total_charges,
            net_pnl       = st.net_pnl,
            exit_reason   = st.exit_reason,
            or_high       = st.or_high,
            or_low        = st.or_low,
            entry_time    = to_dt(entry_ts),
            exit_time     = to_dt(exit_ts),
        )

    def _stopped(self) -> bool:
        return self._stop_event.is_set()

    def fetch_chart_data(self):
        """Fetches 5-minute and 1-minute historical candles for the UI chart.

        Loads TWO sessions (previous trading day + current/most-recent day) so
        the NIFTY chart always shows prior-session context, just like TradingView.
        Previous-day data is fetched once and cached; only the current day is
        re-fetched on each 15-second poll cycle.

        When the market is closed (pre-open, post-close, weekend, holiday) we
        fall back to the most recent day that has data so the chart is never blank.
        """
        def to_candles(records):
            return [
                {
                    "time": int(r["date"].timestamp()),
                    "open": r["open"], "high": r["high"],
                    "low": r["low"],   "close": r["close"],
                }
                for r in records
            ]

        now   = _now()
        today = now.date()

        # ── Step 1: Find current trading day ─────────────────────────────────
        current_day = None
        current_5m: list = []
        current_1m: list = []

        for delta in range(8):
            candidate = today - datetime.timedelta(days=delta)
            if candidate > today:
                continue
            start = f"{candidate} 09:15:00"
            end   = f"{candidate} 15:30:00"
            try:
                records_5m = self.broker.get_historical_data(
                    self.config.index_token, start, end, "5minute"
                )
                if records_5m:
                    current_day = candidate
                    current_5m  = to_candles(records_5m)
                    try:
                        records_1m = self.broker.get_historical_data(
                            self.config.index_token, start, end, "minute"
                        )
                        current_1m = to_candles(records_1m)
                    except Exception as e:
                        logger.warning(f"1m chart fetch failed for {candidate}: {e}")
                    if delta > 0:
                        logger.info(
                            f"Market closed — showing NIFTY chart for {candidate}"
                        )
                    break
            except Exception as e:
                logger.warning(f"5m chart fetch failed for {candidate}: {e}")

        if not current_day:
            logger.error("Could not fetch NIFTY chart data for any of the last 7 days.")
            return

        # ── Step 2: Load previous trading day (once, cached) ─────────────────
        # Previous day data is stable (market is closed), so we only fetch it
        # on the first call.  Subsequent 15-second poll cycles skip this block.
        if self._prev_nifty_day is None:
            for delta in range(1, 10):
                candidate = current_day - datetime.timedelta(days=delta)
                start = f"{candidate} 09:15:00"
                end   = f"{candidate} 15:30:00"
                try:
                    records_5m = self.broker.get_historical_data(
                        self.config.index_token, start, end, "5minute"
                    )
                    if records_5m:
                        self._prev_nifty_day = candidate
                        self._prev_nifty_5m  = to_candles(records_5m)
                        try:
                            records_1m = self.broker.get_historical_data(
                                self.config.index_token, start, end, "minute"
                            )
                            self._prev_nifty_1m = to_candles(records_1m)
                        except Exception as e:
                            logger.warning(f"1m prev-day fetch failed for {candidate}: {e}")
                        logger.info(
                            f"Loaded previous trading day for NIFTY chart: {candidate}"
                        )
                        break
                except Exception as e:
                    logger.warning(f"5m prev-day fetch failed for {candidate}: {e}")

        # ── Step 3: Combine previous + current ───────────────────────────────
        self.state.candles    = self._prev_nifty_5m + current_5m
        self.state.candles_1m = self._prev_nifty_1m + current_1m

    def run_backtest(self):
        """BACKTEST mode entry-point.

        The NIFTY chart and all trade stats in BACKTEST mode are driven
        entirely by the date picker via /api/backtest/run → HistoricalBacktester.
        This engine thread is intentionally idle so it does NOT:

        • Call fetch_chart_data() — that would load TODAY's candles into
          state.candles and the poll loop would render today's data in the
          chart even though the date picker shows a historical date.
        • Run the strategy on today's data — that would set today's OR /
          position state and trigger the LOW BALANCE warning (state.balance
          is 0 in BACKTEST mode by design).
        """
        logger.info("BACKTEST mode active — waiting for historical date selection.")
        self.state.status = "Select a date to run backtest"

    def _backfill_session(self):
        """Replay today's 1-min historical ticks so the strategy has correct OR and
        position state when paper/live mode is started mid-session.

        After entry is detected we immediately fetch today's real 1-min option
        OHLC from Kite and feed those prices back into the strategy for all
        remaining ticks.  This prevents the Black-Scholes fallback from firing
        false "Target Hit" exits during the replay (BS can over-estimate the
        option premium at intraday extremes).
        """
        now = _now()
        if now.time() <= datetime.time(9, 20):
            return  # OR window hasn't closed yet — nothing to backfill

        today = now.date()
        end_str = now.strftime("%Y-%m-%d %H:%M:%S")
        try:
            records = self.broker.get_historical_data(
                self.config.index_token,
                f"{today} 09:15:00",
                end_str,
                "minute",
            )
        except Exception as e:
            logger.warning(f"Session backfill failed — strategy starts without OR: {e}")
            return

        if not records:
            return

        logger.info(f"Backfilling {len(records)} ticks to establish OR and position state…")

        # minute-timestamp → real option close price.
        # Populated synchronously once entry is detected; subsequent ticks use
        # real prices.  _has_real_data gates the neighbour-lookup fallback so we
        # only use it after the map is loaded (avoids spurious None→fallback when
        # no position is open yet).
        opt_price_map:  dict  = {}
        _has_real_data: bool  = False   # True once opt_price_map is populated
        _last_real_opt: float = None    # rolling last-seen real price (neighbour fallback)

        for r in records:
            if self._stopped():
                break
            dt  = r["date"]
            ts  = int(dt.timestamp())

            # ── real_opt resolution (three levels) ───────────────────────────
            # 1. Exact timestamp match in the option price map.
            real_opt = opt_price_map.get(ts)

            # 2. If real data is loaded but the exact ts is missing (minute-
            #    boundary rounding or server tz drift), try ±60 s neighbours
            #    first, then fall back to the running last-known price.
            #    This prevents Black-Scholes from firing false exits when only
            #    one or two candles are missing from Kite's API response.
            if real_opt is None and _has_real_data:
                real_opt = (opt_price_map.get(ts - 60)
                            or opt_price_map.get(ts + 60)
                            or _last_real_opt)
                if real_opt is not None:
                    logger.debug(
                        f"Backfill ts {ts}: exact opt price missing — "
                        f"using fallback ₹{real_opt:.2f}"
                    )

            # 3. Track rolling last-known price for the next iteration's fallback.
            if real_opt is not None:
                _last_real_opt = real_opt

            signal = self.strategy.process_tick(
                ts, dt.time(),
                r["open"], r["high"], r["low"], r["close"],
                real_opt,
            )
            if signal:
                self._handle_signal(signal)

                if signal["action"] == "BUY":
                    # Entry detected — load today's real 1m option data so all
                    # remaining backfill ticks use actual market prices instead
                    # of Black-Scholes.
                    # Use get_expiry_date() so we fetch the SAME contract as the
                    # live order (e.g. June 2 weekly), not the nearest calendar
                    # expiry (e.g. May 26 monthly) which has different prices.
                    strike   = self.strategy.strike
                    opt_type = "CE" if self.state.position_type == "CALL" else "PE"
                    try:
                        from core.options_math import OptionsMath
                        min_expiry = OptionsMath.get_expiry_date(today)
                        opt_records, _contract = self.broker.get_option_history(
                            strike, opt_type, today, min_expiry=min_expiry
                        )
                        for opt_r in opt_records:
                            opt_ts = int(opt_r["date"].timestamp())
                            opt_price_map[opt_ts] = opt_r["close"]
                        _has_real_data = len(opt_price_map) > 0

                        # Override the BS-estimated entry premium with the real
                        # option price at the entry tick.
                        # Try exact ts first, then ±60 s neighbours (same logic
                        # as the per-tick fallback above).
                        entry_real = (opt_price_map.get(ts)
                                      or opt_price_map.get(ts - 60)
                                      or opt_price_map.get(ts + 60))
                        if entry_real:
                            _last_real_opt = entry_real
                            ep = round(entry_real, 2)
                            self.state.entry_prem          = ep
                            self.strategy.target_prem      = round(entry_real + self.config.target_pts, 2)
                            self.state.target_prem         = self.strategy.target_prem
                            # Fix the opening candle on the option chart
                            if self.state.option_prices:
                                self.state.option_prices[0] = {
                                    **self.state.option_prices[0],
                                    "open": ep, "high": ep, "low": ep, "close": ep,
                                }
                            # Fix the BUY marker label on the option chart
                            pt = self.state.position_type
                            for m in reversed(self.state.option_markers):
                                if "BUY" in m.get("text", ""):
                                    m["text"] = f"BUY {pt} @ ₹{ep:.0f}"
                                    break
                            logger.info(
                                f"Backfill: real entry ₹{entry_real:.2f} "
                                f"(BS was ₹{signal['price']:.2f}) | "
                                f"target ₹{self.strategy.target_prem:.2f}"
                            )

                            # ── Back-solve implied IV to calibrate BS fallback ──
                            # When future ticks have no real option price we use
                            # Black-Scholes.  Back-solving IV from the real entry
                            # price makes BS match actual market conditions and
                            # avoids over-estimated prices from the static default.
                            try:
                                is_call = self.state.position_type == "CALL"
                                implied_iv = OptionsMath.implied_vol(
                                    entry_real,
                                    float(self.state.entry_nifty_px),
                                    float(self.strategy.strike),
                                    4 / 365.25,
                                    self.config.risk_free_rate,
                                    is_call=is_call,
                                )
                                if 0.02 <= implied_iv <= 2.0:
                                    old_iv = self.config.assumed_iv
                                    self.config.assumed_iv = round(implied_iv, 4)
                                    logger.info(
                                        f"Backfill: IV back-solved = {implied_iv:.1%} "
                                        f"(was {old_iv:.1%}) — BS fallback calibrated"
                                    )
                            except Exception as _iv_err:
                                logger.debug(f"Backfill: IV back-solve skipped: {_iv_err}")

                        logger.info(
                            f"Backfill: loaded {len(opt_price_map)} real option ticks "
                            f"for NIFTY{strike}{opt_type} — Black-Scholes disabled"
                        )
                    except Exception as e:
                        logger.warning(
                            f"Backfill: could not load real option data "
                            f"(falling back to Black-Scholes): {e}"
                        )
                    # Do NOT break — continue replaying remaining ticks with real prices

                elif signal["action"] == "SELL":
                    logger.info("Trade already completed in backfill — entering monitoring state.")
                    break

        logger.info(
            f"Backfill done. OR={self.state.or_high:.2f}/{self.state.or_low:.2f} "
            f"Position={self.state.position_type}"
        )

    def _fetch_balance(self, real_money: bool):
        """Fetch real funds (LIVE) or keep a paper-mode simulated balance."""
        if real_money:
            funds = self.broker.get_funds()
            self.state.balance = funds["available"]
        else:
            # Paper mode: start with a simulated ₹1,00,000 if not already set
            if self.state.balance == 0.0:
                self.state.balance = 100_000.0

    def _check_balance(self, required: float, real_money: bool) -> bool:
        """
        Returns True if enough balance is available.
        Logs a warning + adds to state.logs if balance is low.
        """
        # Balance is not applicable in BACKTEST mode (state.balance is always 0
        # there by design) — skip the check entirely to avoid false warnings.
        if self.state.app_mode == "BACKTEST":
            return True

        if not real_money:
            # Paper: check against simulated balance
            available = self.state.balance
        else:
            funds = self.broker.get_funds()
            self.state.balance = funds["available"]
            available = funds["available"]

        if available < required:
            msg = (
                f"⚠ LOW BALANCE: Available ₹{available:,.0f} < "
                f"Required ₹{required:,.0f}. Trade may be rejected."
            )
            logger.warning(msg)
            self.state.logs.append(msg)
            return False
        return True

    def _restore_state_from_trade(self, trade, real_money: bool):
        """Populate BotState from a completed DB Trade so the dashboard shows
        the trade summary, NIFTY markers, and option chart after an engine
        restart instead of an empty screen.

        Called only when _trade_already_completed_today() is True so we never
        overwrite live-session state with stale DB data.
        """
        st  = self.state
        cfg = self.config

        # ── Trade summary numbers ──────────────────────────────────────────────
        st.or_high       = float(trade.or_high    or 0)
        st.or_low        = float(trade.or_low     or 0)
        st.entry_prem    = float(trade.entry_prem or 0)
        st.exit_prem     = float(trade.exit_prem  or 0)
        st.gross_pnl     = float(trade.gross_pnl  or 0)
        st.total_charges = float(trade.charges    or 0)
        st.net_pnl       = float(trade.net_pnl    or 0)
        st.pnl           = st.net_pnl
        st.exit_reason    = trade.exit_reason      or ""
        st.position_type  = trade.position_type    or "NONE"
        st.target_prem    = round(st.entry_prem + cfg.target_pts, 2)
        # Trade is complete — ensure no stale live-stream values bleed through
        # from a previous in-memory session (engine restart leaves these at 0
        # via reset(), but be explicit for safety).
        st.live_pnl          = 0.0
        st.live_option_price = 0.0

        # ── Option label + expiry → triggers frontend to auto-fetch the right chart ──
        if trade.strike and trade.position_type in ("CALL", "PUT"):
            from core.options_math import OptionsMath
            opt_suffix      = "CE" if trade.position_type == "CALL" else "PE"
            st.option_label = f"NIFTY {trade.strike} {opt_suffix}"

            # Resolve expiry for the trade date (holiday-adjusted)
            expiry = OptionsMath.get_expiry_date(trade.date)
            st.option_expiry = f"Exp {expiry.day} {expiry.strftime('%b')}"

            # Tell the frontend which date to fetch option candles for.
            # Without this, _ensureLiveOptHistory defaults to TODAY which shows
            # completely different prices if the trade was on a previous session.
            st.option_chart_date = trade.date.isoformat()

        # ── Rebuild NIFTY chart markers from DB timestamps ────────────────────
        def _to_unix(naive_dt):
            """DB stores naive datetimes in IST — attach timezone and convert."""
            if not naive_dt:
                return None
            aware = naive_dt.replace(tzinfo=_IST)
            return int(aware.timestamp())

        entry_ts = _to_unix(trade.entry_time)
        exit_ts  = _to_unix(trade.exit_time)

        if entry_ts:
            color = "#2962FF" if trade.position_type == "CALL" else "#F23645"
            entry_min = (entry_ts // 60) * 60
            st.markers.append({
                "time":     entry_min,
                "position": "belowBar",
                "color":    color,
                "shape":    "arrowUp",
                "text":     f"BUY {trade.position_type} @ ₹{st.entry_prem:.0f}",
            })
            # Option chart entry marker
            st.option_markers.append({
                "time":     entry_min,
                "position": "belowBar",
                "color":    color,
                "shape":    "arrowUp",
                "text":     f"BUY @ ₹{st.entry_prem:.0f}",
            })
        if exit_ts:
            exit_min = (exit_ts // 60) * 60
            st.markers.append({
                "time":     exit_min,
                "position": "aboveBar",
                "color":    "#FF6B00",
                "shape":    "arrowDown",
                "text":     f"SELL @ ₹{st.exit_prem:.0f} ({st.exit_reason})",
            })
            # Option chart exit marker
            pnl_icon = "✅" if st.net_pnl >= 0 else "🔴"
            st.option_markers.append({
                "time":     exit_min,
                "position": "aboveBar",
                "color":    "#FF6B00",
                "shape":    "arrowDown",
                "text":     f"{pnl_icon} SELL @ ₹{st.exit_prem:.0f}",
            })

        pnl_sign = "+" if st.net_pnl >= 0 else ""
        st.logs.append(
            f"[Restored] {trade.date} | {st.position_type} {trade.strike} "
            f"| Entry ₹{st.entry_prem:.0f} → Exit ₹{st.exit_prem:.0f} "
            f"| {st.exit_reason} | Net P&L {pnl_sign}₹{st.net_pnl:.0f}"
        )
        logger.info(
            f"State restored from DB trade: {trade.position_type} {trade.strike} "
            f"| entry ₹{st.entry_prem} exit ₹{st.exit_prem} "
            f"| Net P&L ₹{st.net_pnl:.2f}"
        )

    def run_live(self, real_money: bool = False):
        mode = "REAL MONEY" if real_money else "PAPER TRADING"
        logger.info(f"Mode: {mode} LIVE — connecting to market.")

        # ── Guard: skip if today's trade is already saved in the DB ─────────
        # The backfill replays today's NIFTY ticks using Black-Scholes option
        # pricing (no real LTPs available at that stage).  Black-Scholes can
        # over-estimate the option premium at extreme intraday swings and fire
        # a false "Target Hit" that never happened in the real market.  If a
        # completed trade record already exists in the DB for today, we skip
        # the entire run so the engine never enters or exits on fake BS prices
        # and never double-counts a trade.
        if self._trade_already_completed_today(real_money):
            logger.info(
                "Today's trade already in DB — skipping run to prevent "
                "double-entry or false Black-Scholes exit."
            )
            self.state.status = "Trade done for today"
            self.strategy.has_traded = True

            # ── Still load NIFTY chart and trade summary from DB ──────────────
            # Without this, an engine restart wipes in-memory state and the
            # dashboard shows a blank chart + empty trade summary.
            self.fetch_chart_data()
            try:
                from db.database import SessionLocal
                from db.models import Trade as TradeModel
                db = SessionLocal()
                try:
                    today    = _now().date()
                    mode_str = "LIVE" if real_money else "PAPER"
                    trade = (
                        db.query(TradeModel)
                        .filter(
                            TradeModel.user_id    == self.user_id,
                            TradeModel.date       == today,
                            TradeModel.trade_mode == mode_str,
                        )
                        .first()
                    )
                    if trade:
                        self._restore_state_from_trade(trade, real_money)
                        # Update status to include P&L summary
                        pnl = trade.net_pnl or 0
                        sign = "+" if pnl >= 0 else ""
                        self.state.status = (
                            f"Trade done for today | "
                            f"{trade.position_type} {trade.strike} | "
                            f"P&L {sign}₹{pnl:.0f}"
                        )
                finally:
                    db.close()
            except Exception as e:
                logger.warning(f"State restore from DB trade failed: {e}")
            return

        self.fetch_chart_data()
        self._fetch_balance(real_money)

        # Pre-warm the NFO instruments cache so the first call to
        # get_option_ltp() at the breakout moment never hits a cold cache.
        # Without this, the instruments load lazily inside find_option_token(),
        # and if the Kite API call is slow or fails at that exact second,
        # get_option_ltp() returns None and the Black-Scholes estimate is
        # saved as the entry price instead of the real market LTP.
        try:
            insts = self.broker.get_nfo_instruments()
            logger.info(f"NFO instrument cache pre-warmed: {len(insts)} contracts")
        except Exception as _e:
            logger.warning(f"NFO pre-warm failed (will retry at breakout): {_e}")

        self._backfill_session()  # establish OR + position before live loop

        # If the day's trade already completed during backfill, stop cleanly.
        if self.strategy.has_traded and not self.strategy.in_position:
            logger.info("Today's trade already completed in backfill replay — engine stopped.")
            self.state.status = "Trade done for today"
            return

        balance_tick = 0   # refresh balance every 60s

        while not self._stopped():
            now_dt = _now()
            t = now_dt.time()

            if t < datetime.time(9, 15):
                self.state.status = "Awaiting Market Open"
                time.sleep(1)
                continue

            try:
                ltp = self.broker.get_ltp(self.config.index_symbol)
                unix_time = int(now_dt.timestamp())

                # ── Build live NIFTY candle (tick-by-tick) ───────────────────
                # Aggregate every-second LTP ticks into 1-minute OHLC so the
                # dashboard chart updates in real-time during the OR window
                # (9:15–9:20) instead of waiting for fetch_chart_data().
                minute_ts = (unix_time // 60) * 60
                lc = self.state.live_nifty_candle
                if lc is None or lc.get("time") != minute_ts:
                    self.state.live_nifty_candle = {
                        "time": minute_ts,
                        "open": ltp, "high": ltp, "low": ltp, "close": ltp,
                    }
                else:
                    lc["high"]  = max(lc["high"], ltp)
                    lc["low"]   = min(lc["low"],  ltp)
                    lc["close"] = ltp
                self.state.live_nifty_ltp = ltp

                # ── Refresh balance every 60 seconds ─────────────────────────
                balance_tick += 1
                if balance_tick >= 60:
                    self._fetch_balance(real_money)
                    balance_tick = 0

                # ── If holding a position: fetch real option LTP + update MTM ─
                real_opt_price = None
                if self.strategy.in_position and self.strategy.strike:
                    suffix = "CE" if self.state.position_type == "CALL" else "PE"
                    real_opt_price = self.broker.get_option_ltp(
                        self.strategy.strike, suffix
                    )
                    if real_opt_price:
                        self.state.live_option_price = real_opt_price
                        # Unrealised P&L = (current_ltp - entry) × qty − estimated charges
                        cfg = self.config
                        gross_live   = (real_opt_price - self.state.entry_prem) * cfg.qty
                        # Quick charge estimate for the open side (buy charges already paid)
                        est_charges  = cfg.brokerage_per_order * 2
                        self.state.live_pnl = round(gross_live - est_charges, 2)
                        self.state.status = (
                            f"IN POSITION | NIFTY: {ltp}  "
                            f"Opt: ₹{real_opt_price:.2f}  "
                            f"MTM: ₹{self.state.live_pnl:+.0f}"
                        )
                    else:
                        self.state.status = f"IN POSITION | NIFTY: {ltp} (option LTP unavailable)"
                else:
                    # No position — clear live MTM fields
                    self.state.live_pnl          = 0.0
                    self.state.live_option_price  = 0.0
                    self.state.status = f"Watching | LTP: {ltp}"

                signal = self.strategy.process_tick(
                    unix_time, t, ltp, ltp, ltp, ltp, real_opt_price
                )

                # ── Trades-enabled gate ──────────────────────────────────────
                # When the user turns "Take Trades" OFF the engine still watches
                # the market and logs breakout signals, but never enters a position.
                # Exits are always honoured if somehow already in a trade.
                if signal and signal["action"] == "BUY" and not getattr(self.state, "trades_enabled", True):
                    t_str = now_dt.strftime("%H:%M:%S")
                    self.state.logs.append(
                        f"[{t_str}] ⏸ {signal['type']} breakout ₹{signal['price']:.0f} "
                        f"— Take Trades is OFF, signal skipped"
                    )
                    # Undo all state mutations made by strategy._look_for_entry
                    self.strategy.in_position = False
                    self.strategy.has_traded  = False
                    self.strategy.strike      = None
                    self.strategy.target_prem = None
                    self.state.position_type  = "NONE"
                    self.state.entry_prem     = 0.0
                    self.state.target_prem    = 0.0
                    self.state.option_prices  = []
                    self.state.option_label   = ""
                    self.state.option_expiry  = ""
                    if self.state.markers:        self.state.markers.pop()
                    if self.state.option_markers: self.state.option_markers.pop()
                    signal = None

                if signal:
                    self._handle_signal(signal, real_money)
                    if signal["action"] == "SELL":
                        self.state.live_pnl         = 0.0
                        self.state.live_option_price = 0.0
                        self._save_trade("LIVE" if real_money else "PAPER")
                        # Paper mode: reflect the closed trade's net P&L in the
                        # simulated balance so the header balance is meaningful.
                        if not real_money:
                            self.state.balance = round(
                                self.state.balance + self.state.net_pnl, 2
                            )
                            logger.info(
                                f"Paper balance updated: ₹{self.state.balance:,.2f} "
                                f"(net P&L ₹{self.state.net_pnl:+.2f})"
                            )
                        # Refresh balance immediately after trade closes so the
                        # dashboard reflects the updated funds without waiting 60s
                        self._fetch_balance(real_money)
                        balance_tick = 0
                        logger.info("Trade complete. Shutting down engine.")
                        break

                # Refresh the NIFTY chart every 15 s during market hours only.
                # Post-close the candles are static — polling outside 09:15–15:35
                # just wastes Kite API rate-limit quota.
                if (now_dt.second % 15 == 0
                        and datetime.time(9, 15) <= t <= datetime.time(15, 35)):
                    self.fetch_chart_data()

                time.sleep(1)

            except Exception as e:
                logger.error(f"Network error: {e}")
                time.sleep(2)

    def _backfill_option_chart(self, strike: int, opt_type: str,
                               trade_date: datetime.date):
        """
        Fetch today's full-day 1m option OHLC (09:15–15:30) from Kite and
        prepend those historical candles to state.option_prices so the chart
        shows the entire session, not just from the entry tick onwards.
        Runs in a background thread — safe to call fire-and-forget.

        Uses get_expiry_date() to find the same contract as the live order
        (e.g. June 2 weekly) instead of letting find_option_contract pick the
        nearest expiry (e.g. May 26 monthly) which has different price levels.
        """
        try:
            from core.options_math import OptionsMath
            min_expiry = OptionsMath.get_expiry_date(trade_date)
            records, _contract = self.broker.get_option_history(
                strike, opt_type, trade_date, min_expiry=min_expiry
            )
            if not records:
                logger.info(
                    f"No option history for NIFTY{strike}{opt_type} on {trade_date} "
                    f"— option chart not backfilled"
                )
                return
            hist = sorted(
                [
                    {
                        "time":  int(r["date"].timestamp()),
                        "open":  r["open"], "high": r["high"],
                        "low":   r["low"],  "close": r["close"],
                    }
                    for r in records
                ],
                key=lambda c: c["time"],
            )
            # Historical OHLC from Kite is authoritative for all completed past
            # minutes.  Preserve live-generated candles that are NEWER than the
            # last historical candle so the live loop's in-flight candles aren't
            # dropped when this background HTTP fetch completes late.
            # Using only the "current open minute" (old logic) dropped any
            # complete-minute candles the live loop generated while we were
            # fetching — those are now preserved.
            last_hist_ts = hist[-1]["time"] if hist else 0
            live_extra = [
                c for c in self.state.option_prices
                if c["time"] > last_hist_ts
            ]
            self.state.option_prices = sorted(
                hist + live_extra,
                key=lambda c: c["time"],
            )
            logger.info(
                f"Option chart backfilled: {len(hist)} historical candles "
                f"(authoritative) + {len(live_extra)} live candle(s) "
                f"newer than last hist for NIFTY{strike}{opt_type}"
            )
        except Exception as e:
            logger.warning(f"Option chart backfill failed for NIFTY{strike}{opt_type}: {e}")

    def _handle_signal(self, signal: dict, real_money: bool = False):
        cfg = self.config
        if signal["action"] == "BUY":
            logger.info(
                f"BREAKOUT — BUY {signal['type']} | Entry: ₹{signal['price']:.2f} "
                f"Risk: ₹{signal['risk']:.2f} Target: ₹{signal['target']:.2f}"
            )
            # ── Balance / margin check before placing order ────────────────────
            # Rough required margin = entry premium × qty (options are fully cash-settled)
            required_margin = round(signal["price"] * cfg.qty, 2)
            self._check_balance(required_margin, real_money)

            # ── Backfill option chart with full-day history (09:15 → now) ──────
            # Runs in background so it doesn't block the live feed.
            _opt_suffix  = "CE" if signal["type"] == "CALL" else "PE"
            _trade_date  = _now().date()
            threading.Thread(
                target=self._backfill_option_chart,
                args=(signal["strike"], _opt_suffix, _trade_date),
                daemon=True,
            ).start()

            if not real_money:
                # ── Paper mode: replace BS-estimated entry with real Kite LTP ──
                # The strategy calculates the entry premium using Black-Scholes
                # at the NIFTY breakout price.  The real market option price is
                # often different.  Fetch it now so the P&L, target, and chart
                # all reflect what you would actually have paid.
                opt_type   = "CE" if signal["type"] == "CALL" else "PE"
                real_entry = self.broker.get_option_ltp(signal["strike"], opt_type)
                if real_entry:
                    old_bs = self.state.entry_prem
                    ep     = round(real_entry, 2)
                    self.state.entry_prem          = ep
                    self.strategy.target_prem      = round(real_entry + cfg.target_pts, 2)
                    self.state.target_prem         = self.strategy.target_prem
                    # Fix the opening candle on the option chart
                    if self.state.option_prices:
                        self.state.option_prices[0] = {
                            **self.state.option_prices[0],
                            "open": ep, "high": ep, "low": ep, "close": ep,
                        }
                    # Fix the BUY marker on the option chart so its label matches
                    # the real entry price (strategy wrote the BS estimate earlier)
                    pt = self.state.position_type
                    for m in reversed(self.state.option_markers):
                        if "BUY" in m.get("text", ""):
                            m["text"] = f"BUY {pt} @ ₹{ep:.0f}"
                            break
                    logger.info(
                        f"Paper entry: BS ₹{old_bs:.2f} → real LTP ₹{real_entry:.2f} | "
                        f"target ₹{self.strategy.target_prem:.2f}"
                    )
                else:
                    # get_option_ltp returned None — NFO instruments cache miss or
                    # Kite API error.  The Black-Scholes estimate stays as entry_prem.
                    # Check logs for instrument-cache or auth errors.
                    logger.warning(
                        f"Paper entry: could not fetch real LTP for "
                        f"NIFTY{signal['strike']}{opt_type} — "
                        f"using BS estimate ₹{self.state.entry_prem:.2f}. "
                        f"Verify NFO instrument cache is populated."
                    )
                    t_str = _now().strftime("%H:%M:%S")
                    self.state.logs.append(
                        f"[{t_str}] ⚠ Entry LTP unavailable — "
                        f"using BS ₹{self.state.entry_prem:.0f} as entry price"
                    )

            if real_money:
                from core.options_math import OptionsMath
                opt_type   = "CE" if signal["type"] == "CALL" else "PE"
                trade_date = _now().date()
                # Use the holiday-adjusted expiry as the minimum so we always
                # resolve to the contract with adequate DTE (not a holiday-
                # truncated near-expiry that expires the previous trading day).
                min_expiry = OptionsMath.get_expiry_date(trade_date)
                sym = self.broker.find_option_tradingsymbol(
                    signal["strike"], opt_type, min_expiry
                )
                if not sym:
                    logger.error(
                        f"Could not resolve NFO tradingsymbol for "
                        f"NIFTY{signal['strike']}{opt_type} — order aborted"
                    )
                    return
                # ── MARKET ORDER (not limit) ──────────────────────────────────
                order_id = self.broker.place_market_order(
                    sym, self.broker.kite.TRANSACTION_TYPE_BUY, cfg.qty
                )
                # Overwrite the BS-estimated entry with the actual exchange fill price
                fill = self.broker.get_fill_price(order_id)
                if fill:
                    self.state.entry_prem = round(fill, 2)
                    self.strategy.state.entry_prem = round(fill, 2)
                    # Shift target so it is relative to the real fill price
                    self.strategy.target_prem = fill + cfg.target_pts
                    self.strategy.state.target_prem = round(self.strategy.target_prem, 2)
                    logger.info(f"Real fill (BUY): ₹{fill:.2f} | Target updated: ₹{self.strategy.target_prem:.2f}")
                # Refresh balance immediately after BUY so used margin shows up
                self._fetch_balance(real_money=True)

        elif signal["action"] == "SELL":
            logger.info(f"{signal['reason']} — Exit: ₹{signal['price']:.2f} | P&L: ₹{signal['pnl']:.2f}")
            if real_money:
                from core.options_math import OptionsMath
                pos_type   = self.state.position_type
                opt_type   = "CE" if pos_type == "CALL" else "PE"
                trade_date = _now().date()
                # Same holiday-adjusted minimum as the BUY — ensures SELL resolves
                # to the exact same contract that was purchased earlier.
                min_expiry = OptionsMath.get_expiry_date(trade_date)
                sym = self.broker.find_option_tradingsymbol(
                    self.strategy.strike, opt_type, min_expiry
                )
                if not sym:
                    logger.error(
                        f"Could not resolve NFO tradingsymbol for "
                        f"NIFTY{self.strategy.strike}{opt_type} — SELL order aborted"
                    )
                    return
                order_id = self.broker.place_market_order(
                    sym, self.broker.kite.TRANSACTION_TYPE_SELL, cfg.qty
                )
                # Recalculate P&L from real fill prices
                fill = self.broker.get_fill_price(order_id)
                if fill:
                    entry = self.state.entry_prem
                    gross = (fill - entry) * cfg.qty
                    buy_val  = entry * cfg.qty
                    sell_val = fill  * cfg.qty
                    turnover = buy_val + sell_val
                    brokerage = cfg.brokerage_per_order * 2
                    stt   = sell_val * cfg.stt_pct
                    exch  = turnover * cfg.exchange_charges_pct
                    gst   = (brokerage + exch) * cfg.gst_pct
                    sebi  = turnover * cfg.sebi_charges_pct
                    stamp = buy_val  * cfg.stamp_duty_pct
                    total_charges = round(brokerage + stt + exch + gst + sebi + stamp, 2)
                    net_pnl = round(gross - total_charges, 2)

                    self.state.exit_prem     = round(fill, 2)
                    self.state.gross_pnl     = round(gross, 2)
                    self.state.total_charges = total_charges
                    self.state.net_pnl       = net_pnl
                    self.state.pnl           = net_pnl
                    logger.info(
                        f"Real fill (SELL): ₹{fill:.2f} | "
                        f"Real Net P&L: ₹{net_pnl:.2f}"
                    )
