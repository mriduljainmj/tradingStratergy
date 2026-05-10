import threading
from dataclasses import dataclass, field
from typing import List


@dataclass
class BotState:
    app_mode: str = "PAPER"
    status: str = "Booting..."
    or_high: float = 0.0
    or_low: float = 0.0
    current_high: float = 0.0
    current_low: float = 0.0
    position_type: str = "NONE"
    entry_prem: float = 0.0
    exit_prem: float = 0.0

    # --- P&L TRACKING ---
    gross_pnl: float = 0.0
    total_charges: float = 0.0
    net_pnl: float = 0.0
    pnl: float = 0.0
    brokerage_breakdown: dict = field(default_factory=dict)

    # --- LIVE / PAPER MTM (unrealised while position is open) ---
    live_pnl: float = 0.0            # unrealised net P&L (after charges estimate)
    live_option_price: float = 0.0   # current option LTP

    # --- ACCOUNT ---
    balance: float = 0.0             # available cash balance (fetched from Kite or paper)

    # --- KITE AUTH ---
    kite_auth_error: bool = False    # True when Kite returns "Incorrect api_key/access_token"

    def __post_init__(self):
        # Give paper mode a default simulated balance on first creation
        if self.app_mode == "PAPER" and self.balance == 0.0:
            self.balance = 100_000.0

    option_prices: List[dict] = field(default_factory=list)   # {"time": int, "value": float}
    option_label: str = ""                                     # e.g. "NIFTY 24000 CE"
    option_expiry: str = ""                                    # e.g. "Exp 8 May"
    target_prem: float = 0.0
    used_real_options: bool = False   # True when real Kite NFO prices were used
    logs: List[str] = field(default_factory=list)
    markers: List[dict] = field(default_factory=list)
    candles: List[dict] = field(default_factory=list)
    candles_1m: List[dict] = field(default_factory=list)

    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def reset(self, new_mode: str):
        """Wipe all trading state for a clean mode switch, preserving the lock."""
        self.app_mode = new_mode
        self.status = "Switching mode..."
        self.or_high = 0.0
        self.or_low = 0.0
        self.current_high = 0.0
        self.current_low = 0.0
        self.position_type = "NONE"
        self.entry_prem = 0.0
        self.exit_prem = 0.0
        self.gross_pnl = 0.0
        self.total_charges = 0.0
        self.net_pnl = 0.0
        self.pnl = 0.0
        self.brokerage_breakdown = {}
        self.live_pnl = 0.0
        self.live_option_price = 0.0
        self.kite_auth_error = False
        # Keep existing balance when switching within live modes; seed paper default
        if new_mode == "PAPER" and self.balance == 0.0:
            self.balance = 100_000.0
        elif new_mode == "BACKTEST":
            self.balance = 0.0   # not applicable in backtest
        self.option_prices = []
        self.option_label = ""
        self.option_expiry = ""
        self.target_prem = 0.0
        self.used_real_options = False
        self.logs = []
        self.markers = []
        self.candles = []
        self.candles_1m = []

    def to_dict(self) -> dict:
        return {
            "app_mode": self.app_mode,
            "status": self.status,
            "or_high": self.or_high,
            "or_low": self.or_low,
            "current_high": self.current_high,
            "current_low": self.current_low,
            "position_type": self.position_type,
            "entry_prem": self.entry_prem,
            "exit_prem": self.exit_prem,
            "gross_pnl": self.gross_pnl,
            "total_charges": self.total_charges,
            "net_pnl": self.net_pnl,
            "pnl": self.net_pnl,
            "brokerage_breakdown": self.brokerage_breakdown,
            "live_pnl": self.live_pnl,
            "live_option_price": self.live_option_price,
            "balance": self.balance,
            "kite_auth_error": self.kite_auth_error,
            "option_prices": list(self.option_prices),
            "option_label": self.option_label,
            "option_expiry": self.option_expiry,
            "target_prem": self.target_prem,
            "used_real_options": self.used_real_options,
            "logs": list(self.logs),
            "markers": list(self.markers),
            "candles": list(self.candles),
            "candles_1m": list(self.candles_1m),
        }
