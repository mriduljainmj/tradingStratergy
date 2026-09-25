from config.settings import TradingConfig
import threading
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class BotState:
    app_mode: str = "PAPER"
    status: str = "Booting..."
    or_high: float = 0.0
    or_low: float = 0.0
    current_high: float = 0.0
    current_low: float = 0.0
    position_type: str = "NONE"
    entry_nifty_px: float = 0.0   # NIFTY price at which the trade was triggered
    exit_nifty_px: float = 0.0    # NIFTY price at which the trade was exited
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
    paper_starting_balance: float = field(default_factory=lambda: TradingConfig().paper_starting_balance)
    balance: float = 0.0             # available cash balance (fetched from Kite or paper)

    # --- KITE AUTH ---
    kite_auth_error: bool = False    # True when Kite returns "Incorrect api_key/access_token"

    # --- ENGINE CONTROLS (user-facing toggles, preserved across mode resets) ---
    trades_enabled: bool = False            # False = monitor only, skip new entries
    manual_action: str = ""                # ENTER_CALL | ENTER_PUT | EXIT (one-shot)
    execution_events: List[dict] = field(default_factory=list)
    active_strategy_id: Optional[int] = None  # DB id of the selected strategy
    trade_direction: str = "BOTH"          # "CALL" | "PUT" | "BOTH"

    # --- LIVE NIFTY TICK CANDLE ---
    # Built second-by-second from LTP in run_live() so the chart updates in
    # real-time instead of waiting 15 s for fetch_chart_data() to refresh.
    live_nifty_candle: Optional[dict] = None  # {"time":minute_ts, "open","high","low","close"}
    live_nifty_ltp: float = 0.0               # latest raw NIFTY LTP

    # --- OPTION CHART DATE ---
    # Normally empty (frontend uses today). Set to YYYY-MM-DD when restoring
    # from a DB trade so the option chart loads the actual trade date rather
    # than today's prices (which can differ completely from the trade session).
    option_chart_date: str = ""

    def __post_init__(self):
        # Give paper mode a default simulated balance on first creation
        if self.app_mode == "PAPER" and self.balance == 0.0:
            self.balance = self.paper_starting_balance

    option_prices: List[dict] = field(default_factory=list)   # {"time": int, "value": float}
    option_label: str = ""                                     # e.g. "NIFTY 24000 CE"
    option_expiry: str = ""                                    # e.g. "Exp 8 May"
    option_token: int = 0                                      # Kite instrument_token for the active option
    target_prem: float = 0.0
    exit_reason: str = ""             # "Target Hit" | "Trailing SL Hit" | "EOD Force Close"
    used_real_options: bool = False   # True when real Kite NFO prices were used
    logs: List[str] = field(default_factory=list)
    markers: List[dict] = field(default_factory=list)         # NIFTY chart markers
    option_markers: List[dict] = field(default_factory=list)  # Options chart markers
    candles: List[dict] = field(default_factory=list)
    candles_1m: List[dict] = field(default_factory=list)

    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def reset(self, new_mode: str):
        """Wipe all trading state for a clean mode switch, preserving the lock.

        trades_enabled, active_strategy_id, and trade_direction are user-facing
        controls and are intentionally NOT reset — users expect them to survive
        mode switches.
        """
        self.app_mode = new_mode
        self.status = "Switching mode..."
        self.or_high = 0.0
        self.or_low = 0.0
        self.current_high = 0.0
        self.current_low = 0.0
        self.position_type = "NONE"
        self.entry_nifty_px = 0.0
        self.exit_nifty_px = 0.0
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
            self.balance = self.paper_starting_balance
        elif new_mode == "BACKTEST":
            self.balance = 0.0   # not applicable in backtest
        self.live_nifty_candle = None
        self.live_nifty_ltp = 0.0
        self.option_chart_date = ""
        self.option_prices = []
        self.option_label = ""
        self.option_expiry = ""
        self.option_token = 0
        self.target_prem = 0.0
        self.exit_reason = ""
        self.used_real_options = False
        self.logs = []
        self.markers = []
        self.option_markers = []
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
            "entry_nifty_px": self.entry_nifty_px,
            "exit_nifty_px": self.exit_nifty_px,
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
            "trades_enabled": self.trades_enabled,
            "execution_events": list(self.execution_events),
            "active_strategy_id": self.active_strategy_id,
            "trade_direction": self.trade_direction,
            "live_nifty_candle": self.live_nifty_candle,
            "live_nifty_ltp": self.live_nifty_ltp,
            "option_chart_date": self.option_chart_date,
            "option_prices": list(self.option_prices),
            "option_label": self.option_label,
            "option_expiry": self.option_expiry,
            "option_token": self.option_token,
            "target_prem": self.target_prem,
            "used_real_options": self.used_real_options,
            "logs": list(self.logs),
            "markers": list(self.markers),
            "option_markers": list(self.option_markers),
            "candles": list(self.candles),
            "candles_1m": list(self.candles_1m),
        }
