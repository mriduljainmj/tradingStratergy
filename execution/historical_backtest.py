import datetime
import logging

from config.settings import TradingConfig
from core.state import BotState
from core.strategy import ORBStrategy
from execution.broker import KiteBroker

logger = logging.getLogger(__name__)

MAX_RANGE_DAYS = 60


class HistoricalBacktester:
    def __init__(self, config: TradingConfig, broker: KiteBroker):
        self.config = config
        self.broker = broker

    def run_day(self, date: datetime.date) -> dict:
        state = BotState(app_mode="BACKTEST")
        strategy = ORBStrategy(self.config, state)

        try:
            records = self.broker.get_historical_data(
                self.config.index_token,
                f"{date} 09:15:00",
                f"{date} 15:30:00",
                "minute",
            )
        except Exception as e:
            logger.error(f"Failed to fetch 1-min data for {date}: {e}")
            return {"error": str(e), "date": str(date)}

        if not records:
            return {"error": "No data returned (market holiday?)", "date": str(date)}

        try:
            chart_records = self.broker.get_historical_data(
                self.config.index_token,
                f"{date} 09:15:00",
                f"{date} 15:30:00",
                "5minute",
            )
            candles = [
                {
                    "time": int(r["date"].timestamp()),
                    "open": r["open"], "high": r["high"],
                    "low": r["low"], "close": r["close"],
                }
                for r in chart_records
            ]
        except Exception:
            candles = []

        for r in records:
            dt = r["date"]
            signal = strategy.process_tick(
                int(dt.timestamp()), dt.time(),
                r["open"], r["high"], r["low"], r["close"],
            )
            if signal and signal["action"] == "SELL":
                break

        trade_taken = bool(state.markers)
        return {
            "date": str(date),
            "candles": candles,
            "markers": state.markers,
            "entry_prem": state.entry_prem,
            "exit_prem": state.exit_prem,
            "gross_pnl": state.gross_pnl,
            "total_charges": state.total_charges,
            "net_pnl": state.net_pnl,
            "pnl": state.net_pnl,
            "brokerage_breakdown": state.brokerage_breakdown,
            "position_type": state.position_type,
            "or_high": state.or_high,
            "or_low": state.or_low,
            "current_high": state.current_high,
            "current_low": state.current_low,
            "option_prices": state.option_prices,
            "option_label": state.option_label,
            "target_prem": state.target_prem,
            "logs": state.logs,
            "trade_taken": trade_taken,
        }

    def run_range(self, from_date: datetime.date, to_date: datetime.date) -> dict:
        delta = (to_date - from_date).days
        if delta > MAX_RANGE_DAYS:
            return {"error": f"Range exceeds {MAX_RANGE_DAYS} days. Please select a shorter window."}
        if from_date > to_date:
            return {"error": "from_date must be before to_date."}

        daily = []
        current = from_date
        while current <= to_date:
            if current.weekday() < 5:
                result = self.run_day(current)
                if "error" not in result:
                    daily.append(result)
            current += datetime.timedelta(days=1)

        if not daily:
            return {"error": "No valid trading days in the selected range."}

        traded = [d for d in daily if d["trade_taken"]]
        wins = [d for d in traded if d["pnl"] > 0]
        total_pnl = sum(d["pnl"] for d in daily)

        running = 0
        cumulative = []
        for d in daily:
            running += d["pnl"]
            # Inject Trade Details for the UI Table
            cumulative.append({
                "date": d["date"],
                "pnl": round(d["pnl"], 2),
                "cumulative": round(running, 2),
                "position_type": d.get("position_type", "NONE"),
                "entry_prem": round(d.get("entry_prem", 0), 2) if d.get("entry_prem") else 0,
                "exit_prem": round(d.get("exit_prem", 0), 2) if d.get("exit_prem") else 0,
                "trade_taken": d.get("trade_taken", False)
            })

        return {
            "from_date": str(from_date),
            "to_date": str(to_date),
            "total_days": len(daily),
            "trade_days": len(traded),
            "wins": len(wins),
            "losses": len(traded) - len(wins),
            "win_rate": round(len(wins) / len(traded) * 100, 1) if traded else 0,
            "total_pnl": round(total_pnl, 2),
            "cumulative": cumulative,
        }
