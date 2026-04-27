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
    pnl: float = 0.0
    option_prices: List[dict] = field(default_factory=list)   # {"time": int, "value": float}
    option_label: str = ""                                     # e.g. "NIFTY26APR24000CE"
    target_prem: float = 0.0
    logs: List[str] = field(default_factory=list)
    markers: List[dict] = field(default_factory=list)
    candles: List[dict] = field(default_factory=list)

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
        self.pnl = 0.0
        self.option_prices = []
        self.option_label = ""
        self.target_prem = 0.0
        self.logs = []
        self.markers = []
        self.candles = []

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
            "pnl": self.pnl,
            "option_prices": list(self.option_prices),
            "option_label": self.option_label,
            "target_prem": self.target_prem,
            "logs": list(self.logs),
            "markers": list(self.markers),
            "candles": list(self.candles),
        }
