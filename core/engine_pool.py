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
        # Apply saved settings for the new mode (config fields + trade_direction)
        self._apply_mode_settings(new_mode)
        self.start(new_mode)

    def _apply_mode_settings(self, mode: str):
        """Load saved settings for *mode* from DB and apply to self.config + self.state."""
        try:
            from db.database import SessionLocal
            from db.models import User
            from config.config_utils import get_mode_settings, apply_config_dict
            db = SessionLocal()
            try:
                user = db.get(User, self.user_id)
                if user and user.settings_json:
                    mode_data = get_mode_settings(user.settings_json, mode)
                    if mode_data:
                        apply_config_dict(self.config, mode_data)
                        self.state.trade_direction = mode_data.get(
                            "trade_direction",
                            getattr(self.state, "trade_direction", "BOTH"),
                        )
                        logger.info(
                            f"Engine: applied {mode} settings "
                            f"(direction={self.state.trade_direction}) "
                            f"for user {self.user_id}"
                        )
            finally:
                db.close()
        except Exception as e:
            logger.warning(
                f"Engine: failed to apply {mode} settings for user {self.user_id}: {e}"
            )

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

        On creation:
          1. User's saved settings are loaded from DB (TradingConfig).
          2. If today's Kite access token is in the DB, the session is restored
             automatically — this handles server-restart recovery so the engine
             and KiteTicker reconnect without the user needing to re-authenticate.
        """
        with self._lock:
            if user_id not in self._engines:
                logger.info(f"EnginePool: creating engine for user_id={user_id}")
                ue = UserEngine(user_id, api_key)
                self._load_settings(user_id, ue)
                self._engines[user_id] = ue
                # Restore Kite session in a background thread so the request
                # that triggered creation isn't blocked by network I/O.
                import threading
                threading.Thread(
                    target=self._restore_kite_session,
                    args=(user_id, ue),
                    daemon=True,
                    name=f"SessionRestore-{user_id}",
                ).start()
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

    def _restore_kite_session(self, user_id: int, ue: UserEngine):
        """
        Try to restore today's Kite session from the encrypted token in the DB.
        Called in a background thread immediately after a new UserEngine is created
        so that server restarts are transparent — users don't need to re-authenticate
        as long as their access token is still valid for today.

        On success:
          • Broker's access token is set and validated (kite.profile() call).
          • Trading engine is started in PAPER mode (if not already running),
            BUT ONLY if no trade has already been completed today — this prevents
            the engine from double-starting (and re-entering the same trade) when
            the user reloads the page after a trade has already finished.
        """
        try:
            from db.database import SessionLocal
            db = SessionLocal()
            try:
                ok = ue.broker.restore_from_db(db, user_id)
                already_traded_today = self._has_trade_today(db, user_id, ue.state.app_mode)
            finally:
                db.close()

            if ok:
                logger.info(
                    f"EnginePool: Kite session auto-restored from DB "
                    f"for user {user_id}."
                )
                ue.state.kite_auth_error = False
                if not ue.is_running:
                    if already_traded_today:
                        logger.info(
                            f"EnginePool: trade already completed today for user "
                            f"{user_id} — skipping auto-start to prevent re-entry."
                        )
                    else:
                        ue.start("PAPER")
            else:
                logger.info(
                    f"EnginePool: No valid token in DB for user {user_id} "
                    f"(user must log in via Kite OAuth)."
                )
        except Exception as e:
            logger.warning(
                f"EnginePool: session restore failed for user {user_id}: {e}"
            )

    def _has_trade_today(self, db, user_id: int, mode: str) -> bool:
        """Return True if the user already has a completed trade recorded today."""
        try:
            import datetime
            from db.models import Trade
            today = datetime.date.today()
            trade = (
                db.query(Trade)
                .filter(
                    Trade.user_id == user_id,
                    Trade.date    == today,
                    Trade.trade_mode == mode,
                )
                .first()
            )
            return trade is not None
        except Exception as e:
            logger.warning(f"EnginePool: _has_trade_today check failed: {e}")
            return False  # safe default: allow start if we can't check

    def _load_settings(self, user_id: int, ue: UserEngine):
        """Load user's saved settings for the initial mode (PAPER) from DB."""
        try:
            from db.database import SessionLocal
            from db.models import User
            from config.config_utils import get_mode_settings, apply_config_dict
            db = SessionLocal()
            try:
                user = db.get(User, user_id)
                if user and user.settings_json:
                    # Engines start in PAPER mode — load PAPER settings as initial config
                    mode_data = get_mode_settings(user.settings_json, "PAPER")
                    if mode_data:
                        apply_config_dict(ue.config, mode_data)
                        ue.state.trade_direction = mode_data.get("trade_direction", "BOTH")
                    logger.info(f"EnginePool: settings loaded for user {user_id}")
            finally:
                db.close()
        except Exception as e:
            logger.warning(f"EnginePool: failed to load settings for user {user_id}: {e}")


# Module-level singleton — imported directly by all blueprints and auth routes
engine_pool = EnginePool()
