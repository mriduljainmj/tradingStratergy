import datetime
import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


@dataclass
class AppConfig:
    mode: str = os.getenv("APP_MODE", "PAPER")  # BACKTEST | PAPER | LIVE
    host: str = os.getenv("DASHBOARD_HOST", "127.0.0.1")
    # Render injects PORT; fall back to DASHBOARD_PORT, then 8080 for local dev
    port: int = int(os.getenv("PORT") or os.getenv("DASHBOARD_PORT") or "8080")


@dataclass
class TradingConfig:
    api_key: str = os.getenv("KITE_API_KEY", "")
    api_secret: str = os.getenv("KITE_API_SECRET", "")

    index_symbol: str = "NSE:NIFTY 50"
    index_token: int = 256265
    lot_size: int = 75          # NIFTY 50 lot size (75 units per lot as of Nov 2024)
    qty_multiplier: int = 1    # number of whole lots (qty = lot_size × qty_multiplier)

    target_pts: int = 130
    fib_trail: float = 0.7

    or_end_time: datetime.time = field(default_factory=lambda: datetime.time(9, 35))    # OR window: first 4 × 5-min candles (9:15–9:35)
    entry_end_time: datetime.time = field(default_factory=lambda: datetime.time(10, 30))
    eod_exit_time: datetime.time = field(default_factory=lambda: datetime.time(12, 30))
    strike_spacing: int = 100   # Nifty strikes rounded to nearest 100
    risk_free_rate: float = 0.065
    assumed_iv: float = 0.15   # fallback BS IV; back-solved from real entry during backfill

    brokerage_per_order: float = 20.0
    stt_pct: float = 0.000625
    exchange_charges_pct: float = 0.00053
    gst_pct: float = 0.18
    sebi_charges_pct: float = 0.000001
    stamp_duty_pct: float = 0.00003

    # Slippage applied to market-style fills (entry, stop-loss, EOD exits) as a
    # fraction of the premium per side.  Target exits are limit-like → no slippage.
    # 0.001 = 0.1%, roughly one tick of spread on an ATM NIFTY weekly.
    slippage_pct: float = 0.001

    # Risk guard: once today's realized loss reaches this many ₹, the engine
    # refuses further entries for the day (paper and live).  0 = disabled.
    max_daily_loss: float = 0.0
    paper_starting_balance: float = 100_000.0

    @property
    def qty(self) -> int:
        return int(self.lot_size * self.qty_multiplier)


@dataclass
class BacktestConfig:
    symbol: str = "^NSEI"
    fetch_period: str = "60d"
    test_period: int = 60
    interval: str = "5m"
    lot_size: int = 130

    stop_loss_pts: int = 20
    target_pts: int = 150
    fib_trail: float = 1

    entry_end_time: datetime.time = field(default_factory=lambda: datetime.time(10, 30))
    eod_exit_time: datetime.time = field(default_factory=lambda: datetime.time(12, 30))

    strike_spacing: int = 100
    risk_free_rate: float = 0.065
    fixed_iv: float = None

    brokerage_per_order: float = 20.0
    stt_pct: float = 0.000625
    exchange_charges_pct: float = 0.00053
    gst_pct: float = 0.18
    sebi_charges_pct: float = 0.000001
    stamp_duty_pct: float = 0.00003
