import datetime
import logging
import threading
import time

from config.settings import TradingConfig
from core.state import BotState
from core.strategy import ORBStrategy
from execution.broker import KiteBroker

logger = logging.getLogger(__name__)


class TradingEngine:
    """Orchestrates data fetching, strategy execution, and order routing."""

    def __init__(self, config: TradingConfig, state: BotState, broker: KiteBroker):
        self.config = config
        self.state = state
        self.broker = broker
        self.strategy = ORBStrategy(config, state)
        self._stop_event = threading.Event()

    def stop(self):
        self._stop_event.set()

    def _stopped(self) -> bool:
        return self._stop_event.is_set()

    def fetch_chart_data(self):
        """Fetches 5-minute historical candles for the UI chart."""
        try:
            today = datetime.datetime.now().date()
            records = self.broker.get_historical_data(
                self.config.index_token,
                f"{today} 09:15:00",
                f"{today} 15:30:00",
                "5minute",
            )
            self.state.candles = [
                {
                    "time": int(r["date"].timestamp()),
                    "open": r["open"],
                    "high": r["high"],
                    "low": r["low"],
                    "close": r["close"],
                }
                for r in records
            ]
        except Exception as e:
            logger.error(f"Error fetching chart data: {e}")

    def run_backtest(self):
        logger.info("Mode: BACKTEST — fetching 1-min historical data.")
        self.fetch_chart_data()

        today = datetime.datetime.now().date()
        records = self.broker.get_historical_data(
            self.config.index_token,
            f"{today} 09:15:00",
            f"{today} 15:30:00",
            "minute",
        )
        for r in records:
            if self._stopped():
                break
            dt = r["date"]
            signal = self.strategy.process_tick(
                int(dt.timestamp()), dt.time(), r["open"], r["high"], r["low"], r["close"]
            )
            if signal:
                self._handle_signal(signal)
                if signal["action"] == "SELL":
                    break

        if self.state.position_type == "NONE":
            logger.info("No trades triggered today.")
        logger.info("Backtest complete.")

    def run_live(self, real_money: bool = False):
        mode = "REAL MONEY" if real_money else "PAPER TRADING"
        logger.info(f"Mode: {mode} LIVE — connecting to market.")
        self.fetch_chart_data()

        while not self._stopped():
            now_dt = datetime.datetime.now()
            t = now_dt.time()

            if t < datetime.time(9, 15):
                self.state.status = "Awaiting Market Open"
                time.sleep(1)
                continue

            try:
                ltp = self.broker.get_ltp(self.config.index_symbol)
                unix_time = int(now_dt.timestamp())
                signal = self.strategy.process_tick(unix_time, t, ltp, ltp, ltp, ltp)

                if signal:
                    self._handle_signal(signal, real_money)
                    if signal["action"] == "SELL":
                        logger.info("Trade complete. Shutting down engine.")
                        break

                self.state.status = f"Tracking | LTP: {ltp}"

                if now_dt.second % 15 == 0:
                    self.fetch_chart_data()

                time.sleep(1)

            except Exception as e:
                logger.error(f"Network error: {e}")
                time.sleep(2)

    def _handle_signal(self, signal: dict, real_money: bool = False):
        cfg = self.config
        if signal["action"] == "BUY":
            logger.info(
                f"BREAKOUT — BUY {signal['type']} | Entry: ₹{signal['price']:.2f} "
                f"Risk: ₹{signal['risk']:.2f} Target: ₹{signal['target']:.2f}"
            )
            if real_money:
                sym = f"{cfg.trading_symbol_prefix}{signal['strike']}{'CE' if signal['type'] == 'CALL' else 'PE'}"
                self.broker.place_market_order(sym, self.broker.kite.TRANSACTION_TYPE_BUY, cfg.qty)

        elif signal["action"] == "SELL":
            logger.info(f"{signal['reason']} — Exit: ₹{signal['price']:.2f} | P&L: ₹{signal['pnl']:.2f}")
            if real_money:
                pos_type = self.state.position_type
                sym = f"{cfg.trading_symbol_prefix}{self.strategy.strike}{'CE' if pos_type == 'CALL' else 'PE'}"
                self.broker.place_market_order(sym, self.broker.kite.TRANSACTION_TYPE_SELL, cfg.qty)
