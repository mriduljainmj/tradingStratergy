import datetime
import logging
import threading
import time

from config.settings import TradingConfig
from core.state import BotState
from core.strategy import ORBStrategy
from execution.broker import KiteBroker
from execution.notify import send_trade_alert

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
        self.execution_blocked = False
        self.strategy = ORBStrategy(config, state)
        self._stop_event = threading.Event()
        self._live_symbol = None
        self._live_quantity = None

        # Previous N trading day candles — fetched once and prepended to today's
        # data so the chart shows multi-day history (like TradingView).
        self._prev_nifty_5m: list = []
        self._prev_nifty_1m: list = []
        self._prev_nifty_day: datetime.date | None = None   # sentinel: None = not yet fetched
        self._PREV_DAYS = 6   # how many prior trading days to load

    def stop(self):
        self._stop_event.set()

    def _trade_already_completed_today(self, real_money: bool) -> bool:
        """Return True if a completed trade for today is already saved in the DB.

        Used as a pre-flight guard in run_live() to prevent double-entry
        when the engine restarts after a trade finishes.
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
        # Tag the trade with the user's active strategy (Phase-2 multi-strategy)
        strat_id, strat_name = None, ""
        try:
            from db.database import SessionLocal as _SL
            from db.models import Strategy as _St
            _db = _SL()
            try:
                _s = (_db.query(_St)
                      .filter_by(user_id=self.user_id, is_active=True).first())
                # The ORB engine trades options — never tag its trades with a
                # selected EQUITY strategy; fall back to the options strategy.
                if _s and (_s.instrument_type or "OPTIONS") != "OPTIONS":
                    _s = (_db.query(_St)
                          .filter_by(user_id=self.user_id, instrument_type="OPTIONS")
                          .first())
                if _s:
                    strat_id, strat_name = _s.id, _s.name
            finally:
                _db.close()
        except Exception:
            pass
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
            strategy_id   = strat_id,
            strategy_name = strat_name,
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

        # ── Step 2: Load previous N trading days (once, cached) ──────────────
        # Previous-day data is stable (market is closed), so we fetch once on
        # the first call and reuse on every subsequent 15-second poll cycle.
        # We issue ONE range request (context_from → yesterday) instead of N
        # individual requests — much faster and kinder to the Kite rate limit.
        if self._prev_nifty_day is None:
            # Walk back from current_day to find the oldest of the N prev days
            context_from = current_day
            prev_count   = 0
            _d           = current_day - datetime.timedelta(days=1)
            while prev_count < self._PREV_DAYS and _d >= current_day - datetime.timedelta(days=30):
                if _d.weekday() < 5:          # Mon–Fri only (rough trading-day check)
                    context_from = _d
                    prev_count  += 1
                _d -= datetime.timedelta(days=1)

            prev_end = current_day - datetime.timedelta(days=1)
            if context_from < current_day:
                try:
                    records_5m = self.broker.get_historical_data(
                        self.config.index_token,
                        f"{context_from} 09:15:00",
                        f"{prev_end} 15:30:00",
                        "5minute",
                    )
                    if records_5m:
                        self._prev_nifty_day = context_from
                        self._prev_nifty_5m  = to_candles(records_5m)
                        logger.info(
                            f"Loaded {self._PREV_DAYS} prev trading days for NIFTY 5M chart "
                            f"({context_from} → {prev_end}): {len(records_5m)} candles"
                        )
                except Exception as e:
                    logger.warning(f"5M prev-days fetch failed: {e}")

                try:
                    records_1m = self.broker.get_historical_data(
                        self.config.index_token,
                        f"{context_from} 09:15:00",
                        f"{prev_end} 15:30:00",
                        "minute",
                    )
                    if records_1m:
                        self._prev_nifty_1m = to_candles(records_1m)
                        logger.info(
                            f"Loaded {self._PREV_DAYS} prev trading days for NIFTY 1M chart: "
                            f"{len(records_1m)} candles"
                        )
                except Exception as e:
                    logger.warning(f"1M prev-days fetch failed: {e}")

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

    def _backfill_session(self, real_money=False):
        """Warm the opening range only; Paper and Live never replay entries."""
        now = _now()
        start = now.replace(hour=9, minute=15, second=0, microsecond=0)
        end = min(now.replace(second=0, microsecond=0),
                  now.replace(hour=self.config.or_end_time.hour,
                              minute=self.config.or_end_time.minute, second=0, microsecond=0))
        if end <= start:
            return
        mode = 'Live' if real_money else 'Paper'
        try:
            records = self.broker.get_historical_data(
                self.config.index_token, start.strftime('%Y-%m-%d %H:%M:%S'),
                end.strftime('%Y-%m-%d %H:%M:%S'), 'minute')
        except Exception as exc:
            raise RuntimeError(f'Opening range history unavailable. {mode} engine remains paused.') from exc
        expected = {int((start + datetime.timedelta(minutes=i)).timestamp())
                    for i in range(int((end-start).total_seconds() // 60))}
        opening = [r for r in records if int(r['date'].timestamp()) in expected]
        if not expected.issubset({int(r['date'].timestamp()) for r in opening}):
            raise RuntimeError(f'Opening range history is incomplete. {mode} engine remains paused.')
        for row in opening:
            self.strategy._update_extremes(row['high'], row['low'])
            self.strategy._update_or(row['high'], row['low'])
        self.state.logs.append(
            f'[{now:%H:%M:%S}] Opening range restored: '
            f'{self.state.or_low:.2f}–{self.state.or_high:.2f}. '
            'Earlier signals were not traded; watching new prices.')

    def _fetch_balance(self, real_money: bool):
        """Fetch real funds (LIVE) or keep a paper-mode simulated balance."""
        if real_money:
            funds = self.broker.get_funds()
            self.state.balance = funds["available"]
        else:
            # Paper mode: start with a simulated ₹1,00,000 if not already set
            if self.state.balance == 0.0:
                self.state.balance = self.config.paper_starting_balance

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
        # Preserve the one-trade-per-day limit across engine restarts.
        if self._trade_already_completed_today(real_money):
            logger.info(
                "Today's trade already in DB — skipping run to prevent "
                "double-entry."
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

        try:
            self._backfill_session(real_money=real_money)
        except RuntimeError as exc:
            self.state.trades_enabled = False
            self.state.status = str(exc)
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
                if getattr(self, 'execution_blocked', False):
                    self._stop_event.wait(2)
                    continue
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
                    if real_money:
                        try:
                            real_opt_price = self.broker.get_ltp('NFO:' + self._live_symbol) if self._live_symbol else None
                        except Exception:
                            logger.warning('Live option quote unavailable; target checks suspended, underlying stops remain active.')
                    else:
                        real_opt_price = self.broker.get_option_ltp(self.strategy.strike, suffix)
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

                # ── Manual override (one-shot from the dashboard) ────────────
                manual = getattr(self.state, "manual_action", "")
                signal = None
                if manual:
                    self.state.manual_action = ""
                    if manual == "EXIT" and self.strategy.in_position:
                        exit_prem = real_opt_price or self.state.live_option_price or self.state.entry_prem
                        signal = self._manual_exit_signal(unix_time, ltp, exit_prem)
                    elif manual in ("ENTER_CALL", "ENTER_PUT"):
                        signal = self.strategy.manual_enter(
                            unix_time, t, ltp, "CALL" if manual == "ENTER_CALL" else "PUT")
                        if signal:
                            self.state.logs.append(
                                f"[{now_dt.strftime('%H:%M:%S')}] ✋ Manual {signal['type']} "
                                f"entry @ NIFTY {ltp:.0f}")

                if signal is None:
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
                    self._undo_entry()
                    signal = None

                if signal:
                    executed = self._handle_signal(signal, real_money)
                    if signal["action"] == "SELL" and executed is not False:
                        self.state.live_pnl         = 0.0
                        self.state.live_option_price = 0.0
                        self._save_trade("LIVE" if real_money else "PAPER")
                        # ── Phone alert: position closed ──────────────────
                        _pnl  = self.state.net_pnl
                        _icon = "✅" if _pnl >= 0 else "🔻"
                        send_trade_alert(
                            f"{_icon} <b>EXIT {self.state.position_type}</b> "
                            f"{self.strategy.strike or ''} "
                            f"({'🔴 LIVE' if real_money else '📋 Paper'})\n"
                            f"{self.state.exit_reason} @ ₹{self.state.exit_prem:,.2f}\n"
                            f"Net P&L: <b>{'+' if _pnl >= 0 else ''}₹{_pnl:,.2f}</b>"
                        )
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
        Fetch today's full-day 1m option OHLC (09:15–15:30) PLUS the previous
        N trading days for the same contract from Kite, and prepend them to
        state.option_prices so the chart shows multi-session context like TradingView.

        Uses get_expiry_date() to find the same contract as the live order
        (e.g. June 2 weekly) instead of letting find_option_contract pick the
        nearest expiry (e.g. May 26 monthly) which has different price levels.
        """
        try:
            from core.options_math import OptionsMath
            min_expiry = OptionsMath.get_expiry_date(trade_date)

            # ── Previous N trading days for the same contract ─────────────────
            prev_candles: list = []
            prev_count = 0
            _pd = trade_date - datetime.timedelta(days=1)
            while prev_count < self._PREV_DAYS:
                if _pd.weekday() < 5:   # Mon-Fri rough check
                    try:
                        prev_recs, _ = self.broker.get_option_history(
                            strike, opt_type, _pd, min_expiry=min_expiry
                        )
                        for r in (prev_recs or []):
                            ts = (int(r["date"].timestamp()) // 60) * 60
                            prev_candles.append({
                                "time": ts,
                                "open": r["open"], "high": r["high"],
                                "low":  r["low"],  "close": r["close"],
                            })
                    except Exception as _e:
                        logger.debug(f"Option prev-day fetch skipped for {_pd}: {_e}")
                    prev_count += 1
                _pd -= datetime.timedelta(days=1)

            # ── Today's full-day data ─────────────────────────────────────────
            records, _contract = self.broker.get_option_history(
                strike, opt_type, trade_date, min_expiry=min_expiry
            )
            if not records:
                logger.info(
                    f"No option history for NIFTY{strike}{opt_type} on {trade_date} "
                    f"— option chart not backfilled"
                )
                if prev_candles:
                    self.state.option_prices = sorted(prev_candles, key=lambda c: c["time"])
                return

            today_hist = sorted(
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

            hist = sorted(prev_candles + today_hist, key=lambda c: c["time"])

            # Historical OHLC from Kite is authoritative for all completed past
            # minutes.  Preserve live-generated candles that are NEWER than the
            # last historical candle so the live loop's in-flight candles aren't
            # dropped when this background HTTP fetch completes late.
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
                f"Option chart backfilled: {len(prev_candles)} prev-day + "
                f"{len(today_hist)} today + {len(live_extra)} live candle(s) "
                f"for NIFTY{strike}{opt_type}"
            )
        except Exception as e:
            logger.warning(f"Option chart backfill failed for NIFTY{strike}{opt_type}: {e}")

    def _todays_realized_pnl(self, real_money: bool) -> float:
        """Sum of today's saved net P&L for this user+mode (₹). 0 if unknown."""
        if not self.user_id:
            return 0.0
        try:
            from db.database import SessionLocal
            from db.models import Trade
            from sqlalchemy import func
            db = SessionLocal()
            try:
                total = (
                    db.query(func.coalesce(func.sum(Trade.net_pnl), 0.0))
                    .filter(
                        Trade.user_id    == self.user_id,
                        Trade.date       == _now().date(),
                        Trade.trade_mode == ("LIVE" if real_money else "PAPER"),
                    )
                    .scalar()
                )
                return float(total or 0.0)
            finally:
                db.close()
        except Exception as e:
            logger.warning(f"_todays_realized_pnl failed: {e}")
            return 0.0

    def _manual_exit_signal(self, unix_time: int, nifty_px: float, exit_prem: float) -> dict:
        """Build a SELL signal for a user-requested exit at the current option
        price, matching the P&L/charges/marker logic of an automatic exit."""
        from core.options_math import OptionsMath
        cfg = self.config
        st  = self.state
        st.exit_reason = "Manual Exit"
        gross = (exit_prem - st.entry_prem) * cfg.qty
        total_charges, breakdown = OptionsMath.charges_breakdown(
            st.entry_prem, exit_prem, cfg.qty, cfg)
        net = round(gross - total_charges, 2)
        st.gross_pnl = round(gross, 2)
        st.total_charges = total_charges
        st.net_pnl = net
        st.pnl = net
        st.exit_prem = exit_prem
        st.brokerage_breakdown = breakdown
        st.exit_nifty_px = nifty_px
        color = "#089981" if net > 0 else "#F23645"
        shape = "arrowUp" if net > 0 else "arrowDown"
        posn  = "belowBar" if net > 0 else "aboveBar"
        st.markers.append({"time": unix_time, "position": posn, "color": color,
                           "shape": shape, "text": f"EXIT @ ₹{nifty_px:.0f}"})
        st.option_markers.append({"time": unix_time, "position": posn, "color": color,
                                  "shape": shape, "text": f"EXIT @ ₹{exit_prem:.0f}"})
        self.strategy.in_position = False
        return {"action": "SELL", "reason": "Manual Exit", "price": exit_prem, "pnl": net}

    def _undo_entry(self):
        """Roll back every state mutation made by strategy._look_for_entry
        when an entry signal is rejected before an order is placed."""
        self.strategy.in_position   = False
        self.strategy.has_traded    = False
        self.strategy.strike        = None
        self.strategy.target_prem   = None
        self.strategy.initial_sl_px = None
        self.state.position_type    = "NONE"
        self.state.entry_prem       = 0.0
        self.state.target_prem      = 0.0
        self.state.option_prices    = []
        self.state.option_label     = ""
        self.state.option_expiry    = ""
        if self.state.markers:        self.state.markers.pop()
        if self.state.option_markers: self.state.option_markers.pop()

    def _handle_signal(self, signal: dict, real_money: bool = False):
        if getattr(self, 'execution_blocked', False):
            return False
        try:
            executed = self._execute_signal(signal, real_money)
        except Exception:
            if not real_money:
                raise
            self.execution_blocked = True
            self.state.trades_enabled = False
            self.state.status = 'Order needs review in Kite — execution paused'
            self.state.logs.append('[--:--:--] ' + self.state.status)
            if signal['action'] == 'SELL':
                self.strategy.in_position = True
                self.state.exit_prem = 0.0
                self.state.net_pnl = self.state.pnl = self.state.gross_pnl = 0.0
                self.state.total_charges = 0.0
                self.state.exit_reason = ''
            logger.exception('Live order could not be confirmed')
            return False
        if executed is not False:
            import uuid
            event = {
                'id': str(uuid.uuid4()), 'mode': 'LIVE' if real_money else 'PAPER',
                'action': signal['action'], 'symbol': self._live_symbol if real_money else self.state.option_label,
                'quantity': self._live_quantity if real_money else self.config.qty,
                'price': self.state.entry_prem if signal['action'] == 'BUY' else self.state.exit_prem,
                'reason': signal.get('reason') or 'Entry executed',
                'time': _now().isoformat(),
            }
            with self.state._lock:
                self.state.execution_events = (self.state.execution_events + [event])[-50:]
        return executed

    def _execute_signal(self, signal: dict, real_money: bool = False):
        cfg = self.config
        if signal["action"] == "BUY":
            logger.info(
                f"BREAKOUT — BUY {signal['type']} | Entry: ₹{signal['price']:.2f} "
                f"Risk: ₹{signal['risk']:.2f} Target: ₹{signal['target']:.2f}"
            )
            # ── Daily loss guard ────────────────────────────────────────────────
            # If today's realized losses already exceed the configured cap,
            # refuse the entry and stand down — protects against repeated
            # engine restarts compounding a bad day.
            max_loss = float(getattr(cfg, "max_daily_loss", 0) or 0)
            if max_loss > 0:
                realized = self._todays_realized_pnl(real_money)
                if realized <= -max_loss:
                    self._undo_entry()
                    self.strategy.has_traded = True
                    t_str = _now().strftime("%H:%M:%S")
                    self.state.logs.append(
                        f"[{t_str}] ⛔ Risk guard: today's loss ₹{-realized:,.0f} "
                        f"≥ cap ₹{max_loss:,.0f} — no more trades today."
                    )
                    logger.warning(
                        f"Risk guard blocked entry: realized ₹{realized:,.2f}, "
                        f"cap ₹{max_loss:,.2f}"
                    )
                    send_trade_alert(
                        f"⛔ <b>Risk guard triggered</b>\n"
                        f"Today's loss ₹{-realized:,.0f} hit your ₹{max_loss:,.0f} cap.\n"
                        f"Trading stopped for the day."
                    )
                    return False

            # ── Balance / margin check before placing order ────────────────────
            # Rough required margin = entry premium × qty (options are fully cash-settled)
            required_margin = round(signal["price"] * cfg.qty, 2)
            if not self._check_balance(required_margin, real_money) and real_money:
                # REAL MONEY: never send an order the broker will reject.
                # Block the entry and stand down for the day — retrying every
                # tick would spam orders/margin calls.
                self._undo_entry()
                self.strategy.has_traded = True
                t_str = _now().strftime("%H:%M:%S")
                self.state.logs.append(
                    f"[{t_str}] 🛑 LIVE entry blocked — insufficient margin "
                    f"(need ₹{required_margin:,.0f}). No further entries today."
                )
                logger.error(
                    f"LIVE entry blocked: required ₹{required_margin:,.0f} "
                    f"exceeds available balance — order NOT placed."
                )
                return False

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
                    # A market buy crosses the spread — model it as slippage on
                    # the LTP, same as the backtester's real-price path.
                    ep     = round(real_entry * (1 + getattr(cfg, "slippage_pct", 0.0)), 2)
                    self.state.entry_prem          = ep
                    self.strategy.target_prem      = round(ep + cfg.target_pts, 2)
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
                    self._undo_entry()
                    raise RuntimeError('Option contract could not be resolved.')
                # ── MARKET ORDER (not limit) ──────────────────────────────────
                from execution.order_safety import confirmed_order
                fill = confirmed_order(self.broker, self.user_id, sym,
                                       self.broker.kite.TRANSACTION_TYPE_BUY, cfg.qty,
                                       strategy_id=self.state.active_strategy_id)
                self._live_symbol = sym
                self._live_quantity = cfg.qty
                # Overwrite the BS-estimated entry with the actual exchange fill price
                if fill:
                    self.state.entry_prem = round(fill, 2)
                    self.strategy.state.entry_prem = round(fill, 2)
                    # Shift target so it is relative to the real fill price
                    self.strategy.target_prem = fill + cfg.target_pts
                    self.strategy.state.target_prem = round(self.strategy.target_prem, 2)
                    logger.info(f"Real fill (BUY): ₹{fill:.2f} | Target updated: ₹{self.strategy.target_prem:.2f}")
                # Refresh balance immediately after BUY so used margin shows up
                self._fetch_balance(real_money=True)

            # ── Phone alert: position opened ─────────────────────────────────
            mode_tag = "🔴 LIVE" if real_money else "📋 Paper"
            send_trade_alert(
                f"🟢 <b>BUY {signal['type']}</b> {self.strategy.strike or ''} ({mode_tag})\n"
                f"Entry ₹{self.state.entry_prem:,.2f} · Target ₹{self.state.target_prem:,.2f}\n"
                f"Qty {cfg.qty}"
            )

        elif signal["action"] == "SELL":
            logger.info(f"{signal['reason']} — Exit: ₹{signal['price']:.2f} | P&L: ₹{signal['pnl']:.2f}")
            if real_money:
                sym = self._live_symbol
                if not self._live_quantity:
                    raise RuntimeError('Live entry quantity is unknown; reconcile in Kite.')
                if not sym:
                    logger.error(
                        f"Could not resolve NFO tradingsymbol for "
                        f"strike {self.strategy.strike} — SELL order aborted"
                    )
                    raise RuntimeError('Exit contract could not be resolved.')
                from execution.order_safety import confirmed_order
                fill = confirmed_order(self.broker, self.user_id, sym,
                                       self.broker.kite.TRANSACTION_TYPE_SELL, self._live_quantity,
                                       strategy_id=self.state.active_strategy_id)
                # Recalculate P&L from real fill prices
                if fill:
                    from core.options_math import OptionsMath as _OM
                    entry = self.state.entry_prem
                    gross = (fill - entry) * self._live_quantity
                    total_charges, breakdown = _OM.charges_breakdown(
                        entry, fill, self._live_quantity, cfg
                    )
                    net_pnl = round(gross - total_charges, 2)

                    self.state.exit_prem     = round(fill, 2)
                    self.state.gross_pnl     = round(gross, 2)
                    self.state.total_charges = total_charges
                    self.state.net_pnl       = net_pnl
                    self.state.pnl           = net_pnl
                    self.state.brokerage_breakdown = breakdown
                    logger.info(
                        f"Real fill (SELL): ₹{fill:.2f} | "
                        f"Real Net P&L: ₹{net_pnl:.2f}"
                    )
