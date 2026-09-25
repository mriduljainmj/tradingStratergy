import datetime
import logging
from typing import Optional

from config.settings import TradingConfig
from core.options_math import OptionsMath
from core.state import BotState

logger = logging.getLogger(__name__)


class ORBStrategy:
    """Opening Range Breakout strategy for Nifty options."""

    def __init__(self, config: TradingConfig, state: BotState):
        self.config = config
        self.state = state
        self.in_position: bool = False
        self.has_traded: bool = False   # True after first entry — prevents re-entry
        self.target_prem: Optional[float] = None
        self.strike: Optional[int] = None
        self._expiry_date: Optional[datetime.date] = None  # set at entry, used for DTE calc
        # Hard initial stop on the NIFTY price (OR level / entry ± 40), set at
        # entry.  Governs until the Fib trail becomes tighter.
        self.initial_sl_px: Optional[float] = None

    def process_tick(
        self,
        unix_time: int,
        t: datetime.time,
        tick_open: float,
        tick_high: float,
        tick_low: float,
        tick_close: float,
        real_option_price: Optional[float] = None,
    ) -> Optional[dict]:
        """
        Process one market tick.

        `real_option_price` — when set (paper / live mode), the actual LTP of the
        options contract fetched from the exchange replaces Black-Scholes pricing
        for the chart and target check.  Trailing-SL logic always uses the NIFTY
        price regardless.
        """
        self._update_extremes(tick_high, tick_low)

        if t < self.config.or_end_time:
            self._update_or(tick_high, tick_low)
            return None

        if (
            t == self.config.or_end_time
            and not self.in_position
            and self.state.or_high > 0
            and "OR Locked" not in str(self.state.logs)
        ):
            logger.info(f"OR Locked at {self.config.or_end_time}. High: {self.state.or_high:.2f}, Low: {self.state.or_low:.2f}")

        if self.in_position:
            return self._manage_position(
                unix_time, t, tick_open, tick_high, tick_low, tick_close,
                real_option_price,
            )

        if not self.in_position and not self.has_traded and t <= self.config.entry_end_time:
            return self._look_for_entry(unix_time, t, tick_open, tick_high, tick_low)

        return None

    def _update_extremes(self, tick_high: float, tick_low: float):
        if self.state.current_high == 0:
            self.state.current_high = tick_high
        if self.state.current_low == 0:
            self.state.current_low = tick_low
        self.state.current_high = max(self.state.current_high, tick_high)
        self.state.current_low = min(self.state.current_low, tick_low)

    def _update_or(self, tick_high: float, tick_low: float):
        self.state.or_high = max(self.state.or_high, tick_high) if self.state.or_high > 0 else tick_high
        self.state.or_low = min(self.state.or_low, tick_low) if self.state.or_low > 0 else tick_low

    def _manage_position(
        self,
        unix_time: int,
        t: datetime.time,
        tick_open: float,
        tick_high: float,
        tick_low: float,
        tick_close: float,
        real_option_price: Optional[float] = None,
    ) -> Optional[dict]:
        # Use actual days-to-expiry for Black-Scholes so pricing is proportional
        # to real DTE (e.g. 11 days for a June 2 weekly) rather than a hardcoded
        # 4 days that was left over from the old Thursday-expiry era.
        if self._expiry_date is not None:
            _IST_tz   = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
            tick_date = datetime.datetime.fromtimestamp(unix_time, tz=_IST_tz).date()
            dte       = max((self._expiry_date - tick_date).days, 0.5)
            T_current = dte / 365.25
        else:
            T_current = 4 / 365.25   # safe fallback before entry
        cfg = self.config
        is_call = self.state.position_type == "CALL"
        bs = OptionsMath.bs_call if is_call else OptionsMath.bs_put

        if real_option_price is not None:
            # ── Real exchange price (paper / live mode) ───────────────────────
            # We only have a single LTP per tick, so OHLC all equal the LTP.
            open_p = high_p = low_p = close_p = real_option_price
        else:
            # ── Black-Scholes fallback (backtest or no real data) ─────────────
            if is_call:
                open_p  = bs(tick_open,  self.strike, T_current, cfg.risk_free_rate, cfg.assumed_iv)
                high_p  = bs(tick_high,  self.strike, T_current, cfg.risk_free_rate, cfg.assumed_iv)
                low_p   = bs(tick_low,   self.strike, T_current, cfg.risk_free_rate, cfg.assumed_iv)
                close_p = bs(tick_close, self.strike, T_current, cfg.risk_free_rate, cfg.assumed_iv)
            else:
                open_p  = bs(tick_open,  self.strike, T_current, cfg.risk_free_rate, cfg.assumed_iv)
                high_p  = bs(tick_low,   self.strike, T_current, cfg.risk_free_rate, cfg.assumed_iv)
                low_p   = bs(tick_high,  self.strike, T_current, cfg.risk_free_rate, cfg.assumed_iv)
                close_p = bs(tick_close, self.strike, T_current, cfg.risk_free_rate, cfg.assumed_iv)

        # ── Build proper 1-minute OHLC candles ────────────────────────────────
        # The live loop fires every second, so we must aggregate ticks into
        # 1-minute buckets rather than appending a new "candle" every second.
        # Round unix_time DOWN to the minute start so every second in the same
        # minute shares the same timestamp — identical to Kite's candle format.
        # For backtest/backfill the input is already 1-minute data, so
        # minute_ts == unix_time and the behaviour is unchanged.
        minute_ts = (unix_time // 60) * 60
        if self.state.option_prices and self.state.option_prices[-1]["time"] == minute_ts:
            # Same minute — update high / low / close in-place
            cur = self.state.option_prices[-1]
            cur["high"]  = max(cur["high"],  round(high_p,  2))
            cur["low"]   = min(cur["low"],   round(low_p,   2))
            cur["close"] = round(close_p, 2)
        else:
            # New minute — open a fresh candle
            self.state.option_prices.append({
                "time":  minute_ts,
                "open":  round(open_p,  2),
                "high":  round(high_p,  2),
                "low":   round(low_p,   2),
                "close": round(close_p, 2),
            })

        # ── Stop logic ────────────────────────────────────────────────────────
        # Two stops protect the position; the TIGHTER one governs at any moment:
        #   1. Initial hard SL (set at entry: OR level / entry ± 40 on NIFTY)
        #   2. Fib trailing SL anchored to the day's swing range
        # CALL: stop = max(initial, trail); exit if NIFTY low pierces it.
        # PUT : stop = min(initial, trail); exit if NIFTY high pierces it.
        h, l = self.state.current_high, self.state.current_low
        rng = h - l
        triggered, exit_prem = None, None
        slip = getattr(cfg, "slippage_pct", 0.0)

        if is_call:
            stop_px, stop_label = self.initial_sl_px, "Stop Loss Hit"
            if rng > 0:
                trail = h - rng * cfg.fib_trail
                if stop_px is None or trail > stop_px:
                    stop_px, stop_label = trail, "Trailing SL Hit"
            if stop_px is not None and tick_low <= stop_px:
                triggered = stop_label
                # Fill price: real LTP when we have live data; otherwise the
                # option value AT the stop level (a market order fires the
                # moment the level breaks — not at the bar's close).
                raw_exit = close_p if real_option_price is not None else bs(
                    stop_px, self.strike, T_current, cfg.risk_free_rate, cfg.assumed_iv
                )
                exit_prem = raw_exit * (1 - slip)
        else:
            stop_px, stop_label = self.initial_sl_px, "Stop Loss Hit"
            if rng > 0:
                trail = l + rng * cfg.fib_trail
                if stop_px is None or trail < stop_px:
                    stop_px, stop_label = trail, "Trailing SL Hit"
            if stop_px is not None and tick_high >= stop_px:
                triggered = stop_label
                raw_exit = close_p if real_option_price is not None else bs(
                    stop_px, self.strike, T_current, cfg.risk_free_rate, cfg.assumed_iv
                )
                exit_prem = raw_exit * (1 - slip)

        target_price_verified = self.state.app_mode != 'LIVE' or real_option_price is not None
        if not triggered and target_price_verified and high_p >= self.target_prem:
            # Limit-like exit at the target price — no slippage.
            triggered, exit_prem = "Target Hit", self.target_prem
        elif not triggered and t >= cfg.eod_exit_time:
            # Market exit at close — slippage applies.
            triggered, exit_prem = "EOD Force Close", close_p * (1 - slip)

        if triggered:
            self.state.exit_reason = triggered   # persist for analytics save
            gross_pnl = (exit_prem - self.state.entry_prem) * cfg.qty
            total_charges, breakdown = OptionsMath.charges_breakdown(
                self.state.entry_prem, exit_prem, cfg.qty, cfg
            )
            net_pnl = round(gross_pnl - total_charges, 2)

            self.state.gross_pnl = round(gross_pnl, 2)
            self.state.total_charges = total_charges
            self.state.net_pnl = net_pnl
            self.state.pnl = net_pnl
            self.state.exit_prem = exit_prem
            self.state.brokerage_breakdown = breakdown

            self.state.option_prices[-1]["close"] = round(exit_prem, 2)
            _exit_color = "#089981" if net_pnl > 0 else "#F23645"
            _exit_shape = "arrowUp" if net_pnl > 0 else "arrowDown"
            _exit_pos   = "belowBar" if net_pnl > 0 else "aboveBar"
            # Determine the NIFTY price at exit and persist on state
            nifty_exit_px = stop_px if triggered in ("Trailing SL Hit", "Stop Loss Hit") else tick_close
            self.state.exit_nifty_px = nifty_exit_px
            self.state.markers.append({        # NIFTY chart — show NIFTY exit price
                "time": unix_time, "position": _exit_pos,
                "color": _exit_color, "shape": _exit_shape,
                "text": f"EXIT @ ₹{nifty_exit_px:.0f}",
            })
            self.state.option_markers.append({ # Options chart — show option exit premium
                "time": unix_time, "position": _exit_pos,
                "color": _exit_color, "shape": _exit_shape,
                "text": f"EXIT @ ₹{exit_prem:.0f}",
            })
            self.in_position = False
            return {"action": "SELL", "reason": triggered, "price": exit_prem, "pnl": net_pnl}

        return None

    def _look_for_entry(
        self,
        unix_time: int,
        t: datetime.time,
        tick_open: float,
        tick_high: float,
        tick_low: float,
    ) -> Optional[dict]:
        cfg = self.config

        # Never enter without a locked opening range.  Without this guard an
        # engine started mid-session whose backfill failed sees or_high == 0
        # and the first tick "breaks out" immediately.
        if self.state.or_high <= 0 or self.state.or_low <= 0:
            return None

        direction = getattr(self.state, "trade_direction", "BOTH").upper()

        # ── Compute actual DTE so Black-Scholes uses the real time-value ─────────
        # Hardcoding T=4/365 was fine when NIFTY expiry was always Thursday (≈4 DTE
        # for a Monday/Tuesday trade).  With Tuesday expiry and holiday adjustments
        # the true DTE can be 1–14 days, making the hardcoded value badly wrong.
        # Example: May 22 trade with June 2 expiry → 11 DTE, not 4.
        _IST_tz    = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
        trade_date = datetime.datetime.fromtimestamp(unix_time, tz=_IST_tz).date()
        expiry     = OptionsMath.get_expiry_date(trade_date)
        dte        = max((expiry - trade_date).days, 1)   # at least 1 day floor
        T_entry    = dte / 365.25
        # Store expiry so _manage_position can use the same DTE for BS fallback.
        self._expiry_date = expiry
        logger.debug(f"Entry DTE={dte} ({trade_date} → expiry {expiry}), T_entry={T_entry:.5f}")

        expiry_str = f"Exp {expiry.day} {expiry.strftime('%b')}"   # e.g. "Exp 2 Jun"

        if tick_high > self.state.or_high:
            if direction == "PUT":
                return None   # user restricted to PUT-only; skip CALL breakout
            entry_px = max(tick_open, self.state.or_high)
            self.strike = OptionsMath.get_atm_strike(entry_px, cfg.strike_spacing)
            entry_prem = OptionsMath.bs_call(entry_px, self.strike, T_entry, cfg.risk_free_rate, cfg.assumed_iv)
            # Initial hard SL on NIFTY — the wider of OR-low / entry − 40 pts.
            self.initial_sl_px = min(self.state.or_low, entry_px - 40)
            sl_prem = OptionsMath.bs_call(
                self.initial_sl_px, self.strike, T_entry, cfg.risk_free_rate, cfg.assumed_iv
            )
            self.state.position_type = "CALL"

        elif tick_low < self.state.or_low:
            if direction == "CALL":
                return None   # user restricted to CALL-only; skip PUT breakout
            entry_px = min(tick_open, self.state.or_low)
            self.strike = OptionsMath.get_atm_strike(entry_px, cfg.strike_spacing)
            entry_prem = OptionsMath.bs_put(entry_px, self.strike, T_entry, cfg.risk_free_rate, cfg.assumed_iv)
            self.initial_sl_px = max(self.state.or_high, entry_px + 40)
            sl_prem = OptionsMath.bs_put(
                self.initial_sl_px, self.strike, T_entry, cfg.risk_free_rate, cfg.assumed_iv
            )
            self.state.position_type = "PUT"
        else:
            return None

        # Market-order entry pays the spread — model it as slippage.
        entry_prem = entry_prem * (1 + getattr(cfg, "slippage_pct", 0.0))
        prem_risk = entry_prem - sl_prem  # kept for the BUY signal payload only
        self.target_prem = entry_prem + cfg.target_pts
        self.state.entry_prem = entry_prem
        self.state.target_prem = self.target_prem
        suffix = "CE" if self.state.position_type == "CALL" else "PE"
        self.state.option_label  = f"NIFTY {self.strike} {suffix}"
        self.state.option_expiry = expiry_str
        ep = round(entry_prem, 2)
        # Snap to minute boundary so this candle shares the same timestamp as
        # subsequent candles produced by _manage_position (which uses minute_ts).
        # Using raw unix_time here caused out-of-order candles and a broken chart.
        entry_minute_ts = (unix_time // 60) * 60
        self.state.option_prices = [{"time": entry_minute_ts, "open": ep, "high": ep, "low": ep, "close": ep}]

        self.state.entry_nifty_px = entry_px

        color = "#2962FF" if self.state.position_type == "CALL" else "#F23645"
        shape = "arrowUp" if self.state.position_type == "CALL" else "arrowDown"
        pos = "belowBar" if self.state.position_type == "CALL" else "aboveBar"
        pt = self.state.position_type
        self.state.markers.append({        # NIFTY chart — show NIFTY breakout price
            "time": unix_time, "position": pos, "color": color, "shape": shape,
            "text": f"BUY {pt} @ ₹{entry_px:.0f}",
        })
        self.state.option_markers.append({ # Options chart — show option premium
            "time": entry_minute_ts, "position": pos, "color": color, "shape": shape,
            "text": f"BUY {pt} @ ₹{entry_prem:.0f}",
        })

        self.in_position = True
        self.has_traded  = True   # block any further entry for this session
        return {
            "action": "BUY",
            "reason": (f"ORB breakout: NIFTY {entry_px:.2f} "
                       f"{'above range high ' + str(self.state.or_high) if self.state.position_type == 'CALL' else 'below range low ' + str(self.state.or_low)}"),
            "type": self.state.position_type,
            "price": entry_prem,
            "risk": prem_risk,
            "target": self.target_prem,
            "strike": self.strike,
        }

    # ── Manual override ────────────────────────────────────────────────────────

    def manual_enter(self, unix_time: int, t: datetime.time, price: float,
                     direction: str) -> Optional[dict]:
        """Force an immediate CALL/PUT entry at the current NIFTY price,
        bypassing the opening-range breakout. Sets the same state the auto
        entry does so exits, markers and P&L work identically."""
        if self.in_position or self.has_traded:
            return None
        cfg = direction.upper()
        if cfg not in ("CALL", "PUT"):
            return None
        c = self.config
        _IST_tz    = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
        trade_date = datetime.datetime.fromtimestamp(unix_time, tz=_IST_tz).date()
        expiry     = OptionsMath.get_expiry_date(trade_date)
        dte        = max((expiry - trade_date).days, 1)
        T          = dte / 365.25
        self._expiry_date = expiry
        self.strike = OptionsMath.get_atm_strike(price, c.strike_spacing)
        bs = OptionsMath.bs_call if cfg == "CALL" else OptionsMath.bs_put
        entry_prem = bs(price, self.strike, T, c.risk_free_rate, c.assumed_iv)
        entry_prem = entry_prem * (1 + getattr(c, "slippage_pct", 0.0))
        # Initial stop: OR level if we have one, else ±40 pts from entry.
        if cfg == "CALL":
            base = self.state.or_low if self.state.or_low > 0 else price - 40
            self.initial_sl_px = min(base, price - 40)
        else:
            base = self.state.or_high if self.state.or_high > 0 else price + 40
            self.initial_sl_px = max(base, price + 40)
        self.state.position_type = cfg
        self.target_prem = entry_prem + c.target_pts
        self.state.entry_prem = entry_prem
        self.state.target_prem = self.target_prem
        suffix = "CE" if cfg == "CALL" else "PE"
        self.state.option_label  = f"NIFTY {self.strike} {suffix}"
        self.state.option_expiry = f"Exp {expiry.day} {expiry.strftime('%b')}"
        ep = round(entry_prem, 2)
        mts = (unix_time // 60) * 60
        self.state.option_prices = [{"time": mts, "open": ep, "high": ep, "low": ep, "close": ep}]
        self.state.entry_nifty_px = price
        color = "#2962FF" if cfg == "CALL" else "#F23645"
        shape = "arrowUp" if cfg == "CALL" else "arrowDown"
        posn  = "belowBar" if cfg == "CALL" else "aboveBar"
        self.state.markers.append({"time": unix_time, "position": posn, "color": color,
                                   "shape": shape, "text": f"MANUAL {cfg} @ ₹{price:.0f}"})
        self.state.option_markers.append({"time": mts, "position": posn, "color": color,
                                          "shape": shape, "text": f"MANUAL {cfg} @ ₹{entry_prem:.0f}"})
        self.in_position = True
        self.has_traded  = True
        return {"action": "BUY", "reason": "Manual entry requested", "type": cfg, "price": entry_prem,
                "risk": entry_prem, "target": self.target_prem, "strike": self.strike}
