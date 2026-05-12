"""
Standalone DB helper functions that can be called from anywhere
(trading engine, backtest route, etc.) without going through the HTTP API.
"""
import datetime
import logging

logger = logging.getLogger(__name__)


def save_completed_trade(
    *,
    user_id: int,
    trade_mode: str,                  # "PAPER" | "LIVE" | "BACKTEST"
    date: datetime.date,
    position_type: str,               # "CALL" | "PUT"
    entry_prem: float,
    exit_prem: float,
    strike: int,
    quantity: int,
    gross_pnl: float,
    charges: float,
    net_pnl: float,
    exit_reason: str = "",
    or_high: float = 0.0,
    or_low: float = 0.0,
    entry_time: datetime.datetime = None,
    exit_time: datetime.datetime = None,
    symbol: str = "NIFTY",
) -> bool:
    """
    Persist one completed trade to the `trades` table.
    Returns True on success, False on error (never raises).
    """
    if not user_id:
        logger.warning("save_completed_trade: user_id is None — trade not saved.")
        return False

    from db.database import SessionLocal
    from db.models import Trade

    db = SessionLocal()
    try:
        trade = Trade(
            user_id       = user_id,
            date          = date,
            trade_mode    = trade_mode.upper(),
            symbol        = symbol,
            position_type = position_type,
            entry_time    = entry_time,
            exit_time     = exit_time,
            entry_prem    = entry_prem,
            exit_prem     = exit_prem,
            strike        = strike,
            quantity      = quantity,
            gross_pnl     = gross_pnl,
            charges       = charges,
            net_pnl       = net_pnl,
            exit_reason   = exit_reason,
            or_high       = or_high,
            or_low        = or_low,
        )
        db.add(trade)
        db.commit()
        logger.info(
            f"Trade saved — {trade_mode} {position_type} {date} "
            f"net_pnl=₹{net_pnl:.2f} reason={exit_reason}"
        )
        return True
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to save trade to DB: {e}")
        return False
    finally:
        db.close()
