import logging
import pandas as pd

from config.settings import BacktestConfig

logger = logging.getLogger(__name__)


def calc_charges(entry_prem: float, exit_prem: float, lot: int, config: BacktestConfig) -> dict:
    buy_val = entry_prem * lot
    sell_val = exit_prem * lot
    turnover = buy_val + sell_val
    brokerage = config.brokerage_per_order * 2
    stt = sell_val * config.stt_pct
    exch = turnover * config.exchange_charges_pct
    gst = (brokerage + exch) * config.gst_pct
    sebi = turnover * config.sebi_charges_pct
    stamp = buy_val * config.stamp_duty_pct
    return {"Total Charges (₹)": round(brokerage + stt + exch + gst + sebi + stamp, 2)}


def print_stats(trades: pd.DataFrame):
    if trades.empty:
        logger.info("No trades generated.")
        print("No trades generated.")
        return

    wins = trades[trades["Net P&L (₹)"] > 0]
    win_rate = len(wins) / len(trades) * 100
    net_pnl = trades["Net P&L (₹)"].sum()

    print("\n" + "═" * 60)
    print("  BACKTEST RESULTS — ORB PUT Only (Crossing Entry)")
    print("═" * 60)
    print(f"  Total Trades : {len(trades)}")
    print(f"  Win Rate     : {win_rate:.1f}%")
    print(f"  Net P&L      : ₹{net_pnl:,.0f}")
    print("═" * 60 + "\n")

    pd.set_option("display.max_columns", None)
    pd.set_option("display.width", 140)
    print(trades.tail(10).to_string(index=False))
