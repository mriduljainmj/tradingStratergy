import logging
import math
import pandas as pd

from config.settings import BacktestConfig
from backtesting.analytics import calc_charges
from backtesting.data_loader import compute_daily_hv

logger = logging.getLogger(__name__)


def _ncdf(x: float) -> float:
    return 0.5 * math.erfc(-x / math.sqrt(2))


def bs_put(S: float, K: float, T: float, r: float, sigma: float) -> float:
    if T <= 0 or sigma <= 0:
        return max(K - S, 0.05)
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    return max(K * math.exp(-r * T) * _ncdf(-d2) - S * _ncdf(-d1), 0.05)


def atm_strike(price: float, spacing: int = 50) -> int:
    return int(round(price / spacing) * spacing)


def next_thursday(dt) -> object:
    d = dt.date() if hasattr(dt, "date") else dt
    days = (3 - d.weekday()) % 7
    if days == 0:
        days = 7
    return d + pd.Timedelta(days=days)


def tte_years(current_dt, expiry_date) -> float:
    exp = pd.Timestamp(expiry_date).tz_localize("Asia/Kolkata").replace(hour=15, minute=30)
    secs = (exp - current_dt).total_seconds()
    return max(secs / (365.25 * 24 * 3600), 1 / (365.25 * 24 * 60))


def run_backtest(df: pd.DataFrame, config: BacktestConfig) -> pd.DataFrame:
    lot = config.lot_size
    entry_end = config.entry_end_time
    eod = config.eod_exit_time
    r = config.risk_free_rate
    spacing = config.strike_spacing

    daily_hv = compute_daily_hv(df)

    def get_iv(dt) -> float:
        if config.fixed_iv:
            return config.fixed_iv
        try:
            return float(daily_hv.asof(pd.Timestamp(dt.date())))
        except Exception:
            return 0.15

    trades = []
    all_dates = df.index.normalize().unique()
    test_dates = all_dates[-config.test_period:] if len(all_dates) > config.test_period else all_dates
    logger.info(f"Running strategy on the most recent {len(test_dates)} trading days.")

    for day in test_dates:
        day_idx = [df.index.get_loc(i) for i in df[df.index.normalize() == day].index]
        position = None
        entry_idx_px = entry_prem = strike = expiry = entry_time = None
        initial_sl = trail_dist = best_idx_px = tgt_price = iv = None
        or_high, or_low = -float("inf"), float("inf")
        candle_count = 0
        trade_taken_today = False

        for abs_i in day_idx:
            candle = df.iloc[abs_i]
            t = candle.name.time()

            if position == "put":
                trailing_sl = best_idx_px + trail_dist
                triggered = exit_underlying = None

                if candle["High"] >= trailing_sl:
                    triggered, exit_underlying = "Trail SL", trailing_sl
                elif tgt_price and candle["Low"] <= tgt_price:
                    triggered, exit_underlying = "Target", tgt_price
                elif t >= eod:
                    triggered, exit_underlying = "EOD", candle["Close"]
                else:
                    best_idx_px = min(best_idx_px, candle["Low"])

                if triggered:
                    T_exit = tte_years(candle.name, expiry)
                    exit_prem = bs_put(exit_underlying, strike, T_exit, r, iv)
                    pnl = (exit_prem - entry_prem) * lot
                    charges = calc_charges(entry_prem, exit_prem, lot, config)
                    trades.append({
                        "Date": day.date(),
                        "Type": "PUT",
                        "Entry Time": entry_time,
                        "Exit Time": candle.name,
                        "Entry Index": round(entry_idx_px, 2),
                        "Initial SL": round(initial_sl, 2),
                        "Exit Index": round(exit_underlying, 2),
                        "Entry Prem": round(entry_prem, 2),
                        "Exit Prem": round(exit_prem, 2),
                        "Gross P&L (₹)": round(pnl, 2),
                        "Net P&L (₹)": round(pnl - charges["Total Charges (₹)"], 2),
                        "Exit Reason": triggered,
                    })
                    position = None
                continue

            if trade_taken_today:
                continue
            candle_count += 1

            if candle_count == 1:
                or_high, or_low = candle["High"], candle["Low"]
                continue

            if t <= entry_end and candle["Low"] < or_low:
                position = "put"
                initial_sl = or_high
                entry_idx_px = min(candle["Open"], or_low)
                initial_risk = max(initial_sl - entry_idx_px, config.stop_loss_pts)
                tgt_price = entry_idx_px - config.target_pts
                entry_time = candle.name
                trail_dist = initial_risk * config.fib_trail
                best_idx_px = entry_idx_px
                strike = atm_strike(entry_idx_px, spacing)
                expiry = next_thursday(entry_time)
                iv = get_iv(entry_time)
                T_entry = tte_years(entry_time, expiry)
                entry_prem = bs_put(entry_idx_px, strike, T_entry, r, iv)
                trade_taken_today = True

    return pd.DataFrame(trades)
