import logging
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import pandas as pd

logger = logging.getLogger(__name__)


def plot_results(trades: pd.DataFrame):
    if trades.empty:
        logger.info("No trades to plot.")
        return

    cumulative = trades["Net P&L (₹)"].cumsum()
    daily_pnl = trades.groupby("Date")["Net P&L (₹)"].sum()

    fig = plt.figure(figsize=(14, 8))
    fig.suptitle("Nifty ATM Put ORB Strategy — Backtest Results", fontsize=14, fontweight="bold")
    gs = gridspec.GridSpec(2, 2, figure=fig, hspace=0.4, wspace=0.35)

    ax1 = fig.add_subplot(gs[0, :])
    ax1.plot(cumulative.values, color="#2196F3", linewidth=1.8)
    ax1.axhline(0, color="grey", linewidth=0.8, linestyle="--")
    ax1.fill_between(range(len(cumulative)), cumulative.values, 0, where=cumulative.values >= 0, alpha=0.15, color="green")
    ax1.fill_between(range(len(cumulative)), cumulative.values, 0, where=cumulative.values < 0, alpha=0.15, color="red")
    ax1.set_title("Cumulative Net P&L (₹)")
    ax1.set_ylabel("₹")
    ax1.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"₹{x:,.0f}"))

    ax2 = fig.add_subplot(gs[1, 0])
    colors = ["#4CAF50" if v >= 0 else "#F44336" for v in daily_pnl.values]
    ax2.bar(range(len(daily_pnl)), daily_pnl.values, color=colors, width=0.7)
    ax2.axhline(0, color="grey", linewidth=0.8)
    ax2.set_title("Daily Net P&L (₹)")
    ax2.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"₹{x:,.0f}"))

    ax3 = fig.add_subplot(gs[1, 1])
    prem_chg = trades["Exit Prem"] - trades["Entry Prem"]
    ax3.hist(prem_chg[prem_chg >= 0], bins=15, color="#4CAF50", alpha=0.7, label="Wins")
    ax3.hist(prem_chg[prem_chg < 0], bins=15, color="#F44336", alpha=0.7, label="Losses")
    ax3.axvline(0, color="black", linewidth=0.8)
    ax3.set_title("Put Premium Change Distribution")
    ax3.legend()

    plt.tight_layout()
    plt.show()
