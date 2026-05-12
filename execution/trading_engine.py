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

    def stop(self):
        self._stop_event.set()

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
        save_completed_trade(
            user_id       = self.user_id,
            trade_mode    = trade_mode,
            date          = _now().date(),
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

        When the market is closed (pre-open, post-close, weekend, holiday) we
        fall back to the most recent day that has data so the NIFTY chart is
        never empty in PAPER / LIVE mode.
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

        # Try today first, then walk back up to 7 calendar days (covers weekends
        # and single-day NSE holidays) to find the last day with candle data.
        for delta in range(8):
            candidate = today - datetime.timedelta(days=delta)
            # Skip future dates (shouldn't happen, but be safe)
            if candidate > today:
                continue
            start = f"{candidate} 09:15:00"
            end   = f"{candidate} 15:30:00"
            try:
                records_5m = self.broker.get_historical_data(
                    self.config.index_token, start, end, "5minute"
                )
                if records_5m:
                    self.state.candles = to_candles(records_5m)
                    # Also fetch 1m for the same winning date
                    try:
                        records_1m = self.broker.get_historical_data(
                            self.config.index_token, start, end, "minute"
                        )
                        self.state.candles_1m = to_candles(records_1m)
                    except Exception as e:
                        logger.warning(f"1m chart fetch failed for {candidate}: {e}")
                    if delta > 0:
                        logger.info(
                            f"Market closed for today — showing NIFTY chart "
                            f"for last trading day: {candidate}"
                        )
                    return
            except Exception as e:
                logger.warning(f"5m chart fetch failed for {candidate}: {e}")

        logger.error("Could not fetch NIFTY chart data for any of the last 7 days.")

    def run_backtest(self):
        logger.info("Mode: BACKTEST — fetching 1-min historical data.")
        self.fetch_chart_data()

        today = _now().date()
        records = self.broker.get_historical_data(
            self.config.index_token,
            f"{today} 09:15:00",
            f"{today} 15:30:00",
            "minute",
        )
        for r in records:
            if self._stopped():
                break
            dt = r["date"]
            signal = self.strategy.process_tick(
                int(dt.timestamp()), dt.time(), r["open"], r["high"], r["low"], r["close"]
            )
            if signal:
                self._handle_signal(signal)
                if signal["action"] == "SELL":
                    break

        if self.state.position_type == "NONE":
            logger.info("No trades triggered today.")
        logger.info("Backtest complete.")

    def _backfill_session(self):
        """Replay today's 1-min historical ticks so the strategy has correct OR and
        position state when paper/live mode is started mid-session."""
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
        for r in records:
            if self._stopped():
                break
            dt = r["date"]
            signal = self.strategy.process_tick(
                int(dt.timestamp()), dt.time(),
                r["open"], r["high"], r["low"], r["close"],
            )
            if signal:
                self._handle_signal(signal)
                if signal["action"] == "SELL":
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

    def run_live(self, real_money: bool = False):
        mode = "REAL MONEY" if real_money else "PAPER TRADING"
        logger.info(f"Mode: {mode} LIVE — connecting to market.")
        self.fetch_chart_data()
        self._fetch_balance(real_money)
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

                if signal:
                    self._handle_signal(signal, real_money)
                    if signal["action"] == "SELL":
                        self.state.live_pnl         = 0.0
                        self.state.live_option_price = 0.0
                        self._save_trade("LIVE" if real_money else "PAPER")
                        logger.info("Trade complete. Shutting down engine.")
                        break

                if now_dt.second % 15 == 0:
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
        """
        try:
            records = self.broker.get_option_history(strike, opt_type, trade_date)
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
            # Merge: keep historical candles that don't overlap live ticks
            existing_times = {c["time"] for c in self.state.option_prices}
            new_hist = [c for c in hist if c["time"] not in existing_times]
            self.state.option_prices = sorted(
                new_hist + self.state.option_prices,
                key=lambda c: c["time"],
            )
            logger.info(
                f"Option chart backfilled: {len(new_hist)} historical candles "
                f"prepended for NIFTY{strike}{opt_type}"
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

            if real_money:
                opt_type = "CE" if signal["type"] == "CALL" else "PE"
                trade_date = _now().date()
                # Use find_option_tradingsymbol to get the EXACT Kite tradingsymbol
                # (e.g. "NIFTY2651423700PE" for weekly, "NIFTY26MAY23700PE" for monthly).
                # build_nfo_symbol() generates the wrong format for Kite orders.
                sym = self.broker.find_option_tradingsymbol(
                    signal["strike"], opt_type, trade_date
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

        elif signal["action"] == "SELL":
            logger.info(f"{signal['reason']} — Exit: ₹{signal['price']:.2f} | P&L: ₹{signal['pnl']:.2f}")
            if real_money:
                pos_type   = self.state.position_type
                opt_type   = "CE" if pos_type == "CALL" else "PE"
                trade_date = _now().date()
                sym = self.broker.find_option_tradingsymbol(
                    self.strategy.strike, opt_type, trade_date
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
