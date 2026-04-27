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
        self.best_prem: Optional[float] = None
        self.trail_dist_prem: Optional[float] = None
        self.target_prem: Optional[float] = None
        self.strike: Optional[int] = None

    def process_tick(
        self,
        unix_time: int,
        t: datetime.time,
        tick_open: float,
        tick_high: float,
        tick_low: float,
        tick_close: float,
    ) -> Optional[dict]:
        self._update_extremes(tick_high, tick_low)

        if t < datetime.time(9, 20):
            self._update_or(tick_high, tick_low)
            return None

        if (
            t == datetime.time(9, 20)
            and not self.in_position
            and self.state.or_high > 0
            and "OR Locked" not in str(self.state.logs)
        ):
            logger.info(f"OR Locked. High: {self.state.or_high:.2f}, Low: {self.state.or_low:.2f}")

        if self.in_position:
            return self._manage_position(unix_time, t, tick_open, tick_high, tick_low, tick_close)

        if not self.in_position and t <= self.config.entry_end_time:
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
    ) -> Optional[dict]:
        T_current = 4 / 365.25
        cfg = self.config
        is_call = self.state.position_type == "CALL"
        bs = OptionsMath.bs_call if is_call else OptionsMath.bs_put

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

        self.best_prem = max(self.best_prem, high_p)
        trail_sl_prem = self.best_prem - self.trail_dist_prem

        self.state.option_prices.append({
            "time": unix_time,
            "open":  round(open_p,  2),
            "high":  round(high_p,  2),
            "low":   round(low_p,   2),
            "close": round(close_p, 2),
        })

        triggered, exit_prem = None, None
        if low_p <= trail_sl_prem:
            triggered, exit_prem = "Trailing SL Hit", trail_sl_prem
        elif high_p >= self.target_prem:
            triggered, exit_prem = "Target Hit", self.target_prem
        elif t >= cfg.eod_exit_time:
            triggered, exit_prem = "EOD Force Close", close_p

        if triggered:
            # P&L and Charges Math
            gross_pnl = (exit_prem - self.state.entry_prem) * cfg.qty

            buy_val = self.state.entry_prem * cfg.qty
            sell_val = exit_prem * cfg.qty
            turnover = buy_val + sell_val

            brokerage = cfg.brokerage_per_order * 2
            stt = sell_val * cfg.stt_pct
            exch = turnover * cfg.exchange_charges_pct
            gst = (brokerage + exch) * cfg.gst_pct
            sebi = turnover * cfg.sebi_charges_pct
            stamp = buy_val * cfg.stamp_duty_pct

            total_charges = round(brokerage + stt + exch + gst + sebi + stamp, 2)
            net_pnl = round(gross_pnl - total_charges, 2)

            self.state.gross_pnl = round(gross_pnl, 2)
            self.state.total_charges = total_charges
            self.state.net_pnl = net_pnl
            self.state.pnl = net_pnl
            self.state.exit_prem = exit_prem

            self.state.brokerage_breakdown = {
                "Brokerage (₹20/order)": round(brokerage, 2),
                "STT (0.0625% on sell)": round(stt, 2),
                "Exchange (0.053%)": round(exch, 2),
                "GST (18% on Brk+Exc)": round(gst, 2),
                "SEBI (₹10/Cr)": round(sebi, 2),
                "Stamp Duty (0.003% on buy)": round(stamp, 2)
            }

            self.state.option_prices[-1]["close"] = round(exit_prem, 2)
            self.state.markers.append({
                "time": unix_time,
                "position": "belowBar" if net_pnl > 0 else "aboveBar",
                "color": "#089981" if net_pnl > 0 else "#F23645",
                "shape": "arrowUp" if net_pnl > 0 else "arrowDown",
                "text": f"EXIT: ₹{net_pnl:.0f}",
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
        T_entry = 4 / 365.25

        if tick_high > self.state.or_high:
            entry_px = max(tick_open, self.state.or_high)
            self.strike = OptionsMath.get_atm_strike(entry_px, cfg.strike_spacing)
            entry_prem = OptionsMath.bs_call(entry_px, self.strike, T_entry, cfg.risk_free_rate, cfg.assumed_iv)
            sl_prem = OptionsMath.bs_call(
                min(self.state.or_low, entry_px - 40), self.strike, T_entry, cfg.risk_free_rate, cfg.assumed_iv
            )
            self.state.position_type = "CALL"

        elif tick_low < self.state.or_low:
            entry_px = min(tick_open, self.state.or_low)
            self.strike = OptionsMath.get_atm_strike(entry_px, cfg.strike_spacing)
            entry_prem = OptionsMath.bs_put(entry_px, self.strike, T_entry, cfg.risk_free_rate, cfg.assumed_iv)
            sl_prem = OptionsMath.bs_put(
                max(self.state.or_high, entry_px + 40), self.strike, T_entry, cfg.risk_free_rate, cfg.assumed_iv
            )
            self.state.position_type = "PUT"
        else:
            return None

        prem_risk = entry_prem - sl_prem
        self.target_prem = entry_prem + cfg.target_pts
        self.trail_dist_prem = prem_risk * cfg.fib_trail
        self.best_prem = entry_prem
        self.state.entry_prem = entry_prem
        self.state.target_prem = self.target_prem
        suffix = "CE" if self.state.position_type == "CALL" else "PE"
        self.state.option_label = f"NIFTY {self.strike}{suffix}"
        ep = round(entry_prem, 2)
        self.state.option_prices = [{"time": unix_time, "open": ep, "high": ep, "low": ep, "close": ep}]

        color = "#2962FF" if self.state.position_type == "CALL" else "#F23645"
        shape = "arrowUp" if self.state.position_type == "CALL" else "arrowDown"
        pos = "belowBar" if self.state.position_type == "CALL" else "aboveBar"
        self.state.markers.append({
            "time": unix_time, "position": pos, "color": color, "shape": shape,
            "text": f"BUY {self.state.position_type} @ ₹{entry_prem:.0f}",
        })

        self.in_position = True
        return {
            "action": "BUY",
            "type": self.state.position_type,
            "price": entry_prem,
            "risk": prem_risk,
            "target": self.target_prem,
            "strike": self.strike,
        }
