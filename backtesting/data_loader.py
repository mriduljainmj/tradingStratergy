import logging
import numpy as np
import pandas as pd
import yfinance as yf

from config.settings import BacktestConfig

logger = logging.getLogger(__name__)


def load_data(config: BacktestConfig) -> pd.DataFrame:
    logger.info(f"Fetching {config.fetch_period} ({config.interval}) data for {config.symbol}...")
    raw = yf.download(
        config.symbol,
        period=config.fetch_period,
        interval=config.interval,
        auto_adjust=True,
        progress=False,
    )
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.droplevel(1)
    raw.index = pd.to_datetime(raw.index)
    if raw.index.tzinfo is None:
        raw.index = raw.index.tz_localize("UTC").tz_convert("Asia/Kolkata")
    else:
        raw.index = raw.index.tz_convert("Asia/Kolkata")
    raw = raw.between_time("09:15", "15:30")
    return raw.rename(columns=str.capitalize)


def compute_daily_hv(df: pd.DataFrame) -> pd.Series:
    daily = df["Close"].resample("1D").last().dropna()
    log_ret = np.log(daily / daily.shift(1)).dropna()
    hv = log_ret.rolling(20).std() * np.sqrt(252)
    return hv.ffill().fillna(0.15)
