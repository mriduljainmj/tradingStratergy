"""
engine_pool.py — Per-user trading engine container and thread-safe pool.

Each authenticated user gets their own isolated:
  • TradingConfig          — per-user settings (lot size, risk params, etc.)
  • BotState               — per-user live/paper trading state
  • KiteBroker             — per-user Kite Connect session
  • TradingEngine          — per-user strategy execution thread
  • HistoricalBacktester   — per-user historical backtest runner

The EnginePool maps user_id (int) → UserEngine and is shared across
all Flask blueprints as the module-level singleton `engine_pool`.
"""

import logging
import threading
from typing import Optional

logger = logging.getLogger(__name__)


class UserEngine:
    """All per-user trading resources bundled together."""

    def __init__(self, user_id: int, api_key: str = ""):
        from config.settings import TradingConfig
        from core.state import BotState
        from execution.broker import KiteBroker
        from execution.historical_backtest import HistoricalBacktester

        self.user_id    = user_id
        self.config     = TradingConfig(api_key=api_key)
        self.state      = BotState(app_mode="PAPER")
        self.broker     = KiteBroker(self.config)
        self.backtester = HistoricalBacktester(self.config, self.broker)

        self._engine                         = None   # TradingEngine
        self._thread: Optional[threading.Thread] = None

    # ── Engine lifecycle ───────────────────────────────────────────────────────

    def start(self, mode: str):
        """Start (or restart) the trading engine in the given mode."""
        from execution.trading_engine import TradingEngine

        # Stop any currently running engine and wait for its thread to exit
        if self._engine:
            self._engine.stop()
            if self._thread and self._thread.is_alive():
                self._thread.join(timeout=3)

        self._engine = TradingEngine(
            self.config, self.state, self.broker,
            user_id=self.user_id,
        )

        def _run():
            logger.info(f"Engine starting — user={self.user_id} mode={mode}")
            self.state.logs.append(f"[--:--:--] ▶ Starting in {mode} mode")
            if mode == "BACKTEST":
                self._engine.run_backtest()
            elif mode == "PAPER":
                self._engine.run_live(real_money=False)
            elif mode == "LIVE":
                self._engine.run_live(real_money=True)

        self._thread = threading.Thread(
            target=_run,
            daemon=True,
            name=f"Engine-{self.user_id}-{mode}",
        )
        self._thread.start()

    def switch_mode(self, new_mode: str):
        """Reset all trading state and restart the engine in new_mode."""
        logger.info(
            f"Mode switch — user={self.user_id}: "
            f"{self.state.app_mode} → {new_mode}"
        )
        if self._engine:
            self._engine.stop()
        self.state.reset(new_mode)
        self.start(new_mode)

    def stop(self):
        """Signal the running engine to stop."""
        if self._engine:
            self._engine.stop()

    @property
    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())


class EnginePool:
    """Thread-safe pool mapping user_id → UserEngine."""

    def __init__(self):
        self._engines: dict = {}
        self._lock = threading.Lock()

    # ── Public API ─────────────────────────────────────────────────────────────

    def get(self, user_id: int) -> Optional[UserEngine]:
        """Return the UserEngine for user_id, or None if not in pool."""
        return self._engines.get(user_id)

    def get_or_create(self, user_id: int, api_key: str = "") -> UserEngine:
        """Return an existing UserEngine or create and configure a new one.

        On creation the user's saved settings are loaded from the DB so
        TradingConfig immediately reflects their preferences.
        """
        with self._lock:
            if user_id not in self._engines:
                logger.info(f"EnginePool: creating engine for user_id={user_id}")
                ue = UserEngine(user_id, api_key)
                self._load_settings(user_id, ue)
                self._engines[user_id] = ue
            return self._engines[user_id]

    def remove(self, user_id: int):
        """Stop and remove a user's engine from the pool."""
        with self._lock:
            ue = self._engines.pop(user_id, None)
        if ue:
            ue.stop()
            logger.info(f"EnginePool: removed engine for user_id={user_id}")

    def all_engines(self) -> list:
        """Return a snapshot of all active UserEngine instances."""
        return list(self._engines.values())

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _load_settings(self, user_id: int, ue: UserEngine):
        """Load user's saved settings from DB and apply to ue.config."""
        try:
            from db.database import SessionLocal
            from db.models import User
            from config.config_utils import apply_settings_json
            db = SessionLocal()
            try:
                user = db.get(User, user_id)
                if user and user.settings_json:
                    apply_settings_json(ue.config, user.settings_json)
                    logger.info(f"EnginePool: settings loaded for user {user_id}")
            finally:
                db.close()
        except Exception as e:
            logger.warning(f"EnginePool: failed to load settings for user {user_id}: {e}")


# Module-level singleton — imported directly by all blueprints and auth routes
engine_pool = EnginePool()
