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
from functools import wraps
from typing import Optional

logger = logging.getLogger(__name__)


def lifecycle_locked(fn):
    @wraps(fn)
    def wrapper(self, *args, **kwargs):
        with self._lifecycle_lock:
            return fn(self, *args, **kwargs)
    return wrapper


class UserEngine:
    """All per-user trading resources bundled together."""

    def __init__(self, user_id: int, api_key: str = ""):
        from config.settings import TradingConfig
        from core.state import BotState
        from execution.broker import KiteBroker
        from execution.historical_backtest import HistoricalBacktester

        self._lifecycle_lock = threading.RLock()
        self.equity_engines = {}
        self.user_id    = user_id
        self.config     = TradingConfig(api_key=api_key)
        self.state      = BotState(app_mode="PAPER")
        self.broker     = KiteBroker(self.config)
        self.backtester = HistoricalBacktester(self.config, self.broker)

        self._engine                         = None   # TradingEngine
        self._thread: Optional[threading.Thread] = None

    # ── Engine lifecycle ───────────────────────────────────────────────────────

    # ── Phase-2: parallel equity strategy engines ────────────────────────────
    @lifecycle_locked
    def start_equity_strategy(self, strat_dict: dict, paper: bool = True) -> dict:
        """Start (or restart) the EquityEngine for one strategy row."""
        from execution.equity_engine import EquityEngine
        from execution.order_safety import unresolved_orders
        if unresolved_orders(self.user_id):
            raise RuntimeError('Reconcile pending broker orders before starting an engine.')
        if not hasattr(self, "equity_engines"):
            self.equity_engines = {}
        sid = strat_dict["id"]
        eng = self.equity_engines.get(sid)
        if eng and eng.running:
            if eng.paper != paper:
                raise RuntimeError('Stop the strategy before changing its execution mode.')
            return eng.snapshot()
        if eng and (eng.in_pos or getattr(eng, 'execution_blocked', False)):
            raise RuntimeError('Existing strategy exposure must be reconciled first.')
        eng = EquityEngine(strat_dict, self.broker, self.user_id)
        self.equity_engines[sid] = eng
        eng.start(paper=paper)
        return eng.snapshot()

    def restore_running_equity_strategies(self):
        """Restore paper strategies only. Live strategies require explicit restart."""
        try:
            from db.database import SessionLocal
            from db.models import Strategy
            db = SessionLocal()
            try:
                rows = (db.query(Strategy)
                        .filter_by(user_id=self.user_id,
                                   instrument_type="EQUITY",
                                   is_running=True)
                        .all())
                strats = [s.to_dict() | {"run_mode": s.run_mode} for s in rows]
            finally:
                db.close()
            for sd in strats:
                paper = (sd.get("run_mode") or "PAPER") != "LIVE"
                if not paper:
                    with SessionLocal() as session:
                        row = session.get(Strategy, sd['id'])
                        if row:
                            row.is_running = False
                            session.commit()
                    self.state.logs.append(f"Live strategy {sd['name']} requires explicit restart after broker position review")
                    continue
                self.start_equity_strategy(sd, paper=True)
                logger.info(
                    f"EnginePool: restored equity strategy '{sd['name']}' "
                    f"({sd['symbol']}, {'PAPER' if paper else 'LIVE'}) after restart."
                )
                from execution.notify import send_trade_alert
                send_trade_alert(
                    f"🔄 <b>{sd['name']}</b> ({sd['symbol']}) restored after a "
                    f"server restart — running in "
                    f"{'📋 Paper' if paper else '🔴 LIVE'} mode."
                )
        except Exception:
            logger.exception("restore_running_equity_strategies failed")

    @lifecycle_locked
    def stop_equity_strategy(self, sid: int) -> bool:
        eng = getattr(self, "equity_engines", {}).get(sid)
        if eng:
            eng.stop()
            return True
        return False

    def manual_equity_action(self, sid: int, action: str) -> bool:
        eng = getattr(self, "equity_engines", {}).get(sid)
        if eng and eng.running:
            eng.manual(action)
            return True
        return False

    def equity_snapshots(self) -> list:
        return [e.snapshot() for e in getattr(self, "equity_engines", {}).values()]

    @lifecycle_locked
    def start(self, mode: str):
        """Start (or restart) the trading engine in the given mode."""
        from execution.trading_engine import TradingEngine

        from execution.order_safety import unresolved_orders
        if mode not in ('PAPER', 'LIVE', 'BACKTEST'):
            raise ValueError('Unsupported trading mode.')
        if unresolved_orders(self.user_id):
            raise RuntimeError('Reconcile pending broker orders before starting an engine.')
        if self.is_running and self.state.app_mode == mode:
            return
        from execution.order_safety import has_exposure
        if has_exposure(self):
            raise RuntimeError('Existing engine exposure must be reconciled before restarting.')
        if mode == 'LIVE':
            from execution.order_safety import verify_flat_broker
            verify_flat_broker(self.broker)
        self._stop_main()
        self.state.app_mode = mode
        self._engine = TradingEngine(
            self.config, self.state, self.broker,
            user_id=self.user_id,
        )

        engine = self._engine
        def _run():
            logger.info(f"Engine starting — user={self.user_id} mode={mode}")
            self.state.logs.append(f"[--:--:--] ▶ Starting in {mode} mode")
            if mode == "BACKTEST":
                engine.run_backtest()
            elif mode == "PAPER":
                engine.run_live(real_money=False)
            elif mode == "LIVE":
                engine.run_live(real_money=True)

        self._thread = threading.Thread(
            target=_run,
            daemon=True,
            name=f"Engine-{self.user_id}-{mode}",
        )
        self._thread.start()

    @lifecycle_locked
    def switch_mode(self, new_mode: str):
        """Select a mode with execution paused; starting requires a separate action."""
        logger.info(
            f"Mode switch — user={self.user_id}: "
            f"{self.state.app_mode} → {new_mode}"
        )
        from execution.order_safety import has_exposure, unresolved_orders
        if has_exposure(self) or unresolved_orders(self.user_id):
            raise RuntimeError('Close positions and reconcile orders before changing mode.')
        if new_mode not in ('PAPER', 'LIVE', 'BACKTEST'):
            raise ValueError('Unsupported trading mode.')
        self.state.trades_enabled = False
        self._stop_main()
        if has_exposure(self) or unresolved_orders(self.user_id):
            raise RuntimeError('An order completed while stopping. Review the position before changing mode.')
        auth_error = self.state.kite_auth_error
        self.state.reset(new_mode)
        self.state.kite_auth_error = auth_error
        self.state.manual_action = ''
        # Apply saved settings for the new mode (config fields + trade_direction)
        self._apply_mode_settings(new_mode)
        self.state.status = 'Mode selected — trading paused'

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

    def _stop_main(self):
        if self._engine:
            self._engine.stop()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=5)
            if self._thread.is_alive():
                raise RuntimeError('Previous engine is still stopping. Retry shortly.')

    @lifecycle_locked
    def stop(self):
        """Stop every engine before removing credentials or user resources."""
        if getattr(self, '_market_stream', None):
            self._market_stream.close()
        self._stop_main()
        for engine in list(self.equity_engines.values()):
            engine.stop()

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
                ue.state.paper_starting_balance = ue.config.paper_starting_balance
                ue.state.balance = ue.config.paper_starting_balance
                self._engines[user_id] = ue
                # Restore Kite session in a background thread so the request
                # that triggered creation isn't blocked by network I/O.
                import threading
                import os
                if os.getenv('RESTORE_TRADING_SESSIONS', '1') == '1':
                    threading.Thread(
                        target=self._restore_kite_session,
                        args=(user_id, ue),
                        daemon=True,
                        name=f"SessionRestore-{user_id}",
                    ).start()
                else:
                    ue.state.kite_auth_error = True
                    ue.state.trades_enabled = False
                    ue.state.status = 'Kite login needed — session restore disabled'
            return self._engines[user_id]

    def remove(self, user_id: int):
        """Stop and remove a user's engine from the pool."""
        ue = self.get(user_id)
        if ue:
            ue.stop()
            with self._lock:
                if self._engines.get(user_id) is ue:
                    self._engines.pop(user_id, None)
            logger.info(f"EnginePool: removed engine for user_id={user_id}")

    def all_engines(self) -> list:
        """Return a snapshot of all active UserEngine instances."""
        with self._lock:
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
            BUT ONLY if:
            1. background_trading is True in the user's DB record, AND
            2. No trade has already been completed today — this prevents
               double-starting (and re-entering the same trade) when the user
               reloads the page after a trade has already finished.
        """
        try:
            from db.database import SessionLocal
            from db.models import User
            db = SessionLocal()
            try:
                ok = ue.broker.restore_from_db(db, user_id)
                already_traded_today = self._has_trade_today(db, user_id, ue.state.app_mode)
                # Read the background_trading flag from DB
                user = db.get(User, user_id)
                is_admin = bool(user and user.is_admin)
                bg_enabled = bool(
                    user.background_trading
                    if user and user.background_trading is not None
                    else True
                )
            finally:
                db.close()

            if ok:
                logger.info(
                    f"EnginePool: Kite session auto-restored from DB "
                    f"for user {user_id}."
                )
                ue.state.kite_auth_error = False

                # Per-strategy background running: bring back every equity
                # strategy the user had running, regardless of the ORB
                # engine's Auto Trade flag below.
                from execution.order_safety import unresolved_orders
                if unresolved_orders(user_id):
                    ue.state.trades_enabled = False
                    ue.state.status = 'Broker order reconciliation required'
                    return
                import os
                # Operational override for maintenance; saved user preference is unchanged.
                bg_enabled = bg_enabled and os.getenv('AUTO_START_TRADING', '1') == '1'
                if is_admin and bg_enabled:
                    ue.restore_running_equity_strategies()

                if not bg_enabled or not is_admin:
                    logger.info(
                        f"EnginePool: background trading is OFF for user {user_id} "
                        f"— engine will not auto-start. User must enable it from the dashboard."
                    )
                    ue.state.trades_enabled = False
                    if not ue.is_running:
                        ue.state.status = 'Kite connected — trading paused'
                    return

                if not ue.is_running:
                    ue.state.trades_enabled = True
                    if already_traded_today:
                        logger.info(
                            f"EnginePool: trade already completed today for user "
                            f"{user_id} — starting PAPER engine to restore chart & summary."
                        )
                    ue.start("PAPER")
            else:
                logger.info(
                    f"EnginePool: No valid token in DB for user {user_id} "
                    f"(user must log in via Kite OAuth)."
                )
                # Surface it — otherwise the dashboard sits on the default
                # "Booting..." status with a blank chart and no explanation.
                ue.state.kite_auth_error = True
                ue.state.status = "Kite login needed — open Profile"
                ue.state.logs.append(
                    "[--:--:--] 🔑 Kite token expired — open Profile and "
                    "log in to Kite to start today's session."
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
            today = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=5, minutes=30))).date()
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
            raise RuntimeError('Trade history could not be checked; engine remains paused.') from e

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
