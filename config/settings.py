import datetime
import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


@dataclass
class AppConfig:
    mode: str = os.getenv("APP_MODE", "PAPER")  # BACKTEST | PAPER | LIVE
    host: str = os.getenv("DASHBOARD_HOST", "127.0.0.1")
    port: int = int(os.getenv("DASHBOARD_PORT", "8080"))


@dataclass
class TradingConfig:
    api_key: str = os.getenv("KITE_API_KEY", "")
    api_secret: str = os.getenv("KITE_API_SECRET", "")

    index_symbol: str = "NSE:NIFTY 50"
    index_token: int = 256265
    trading_symbol_prefix: str = "NFO:NIFTY26APR"
    lot_size: int = 25
    qty_multiplier: float = 2.6

    target_pts: int = 130
    fib_trail: float = 0.7

    entry_end_time: datetime.time = field(default_factory=lambda: datetime.time(10, 30))
    eod_exit_time: datetime.time = field(default_factory=lambda: datetime.time(12, 30))
    strike_spacing: int = 50
    risk_free_rate: float = 0.065
    assumed_iv: float = 0.15

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

    strike_spacing: int = 50
    risk_free_rate: float = 0.065
    fixed_iv: float = None

    brokerage_per_order: float = 20.0
    stt_pct: float = 0.000625
    exchange_charges_pct: float = 0.00053
    gst_pct: float = 0.18
    sebi_charges_pct: float = 0.000001
    stamp_duty_pct: float = 0.00003
