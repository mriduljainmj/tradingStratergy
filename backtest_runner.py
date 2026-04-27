"""
NiftySwing Backtester Entry Point
-----------------------------------
Usage:
    python backtest_runner.py
"""

import logging
import warnings

warnings.filterwarnings("ignore")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] — %(message)s",
    datefmt="%H:%M:%S",
)

from config.settings import BacktestConfig
from backtesting.data_loader import load_data
from backtesting.engine import run_backtest
from backtesting.analytics import print_stats
from backtesting.visualizer import plot_results


def main():
    config = BacktestConfig()
    df = load_data(config)
    trades = run_backtest(df, config)
    print_stats(trades)
    plot_results(trades)


if __name__ == "__main__":
    main()
