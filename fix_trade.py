"""
One-shot script: fixes trade ID=2 (May 22 PAPER CALL 23700)
by fetching the real 1-min Kite price at entry_time and
rewriting entry_prem / gross_pnl / charges / net_pnl in the DB.

Run from the project root:
    ./venv/bin/python fix_trade.py
"""
import datetime
import sys

# ── DB ──────────────────────────────────────────────────────────────────────
from db.database import SessionLocal
from db.models import Trade

# ── Kite / broker ───────────────────────────────────────────────────────────
from config.settings import TradingConfig
from execution.broker import KiteBroker
from core.options_math import OptionsMath

# ── IST timezone ────────────────────────────────────────────────────────────
_IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

TRADE_ID   = 2
TRADE_DATE = datetime.date(2026, 5, 22)

# ── Brokerage constants (mirror TradingConfig defaults) ──────────────────────
cfg = TradingConfig()


def recalculate(entry_prem: float, exit_prem: float, qty: int) -> dict:
    gross_pnl = round((exit_prem - entry_prem) * qty, 2)
    buy_val   = entry_prem * qty
    sell_val  = exit_prem  * qty
    turnover  = buy_val + sell_val
    brokerage = cfg.brokerage_per_order * 2
    stt       = sell_val * cfg.stt_pct
    exch      = turnover * cfg.exchange_charges_pct
    gst       = (brokerage + exch) * cfg.gst_pct
    sebi      = turnover * cfg.sebi_charges_pct
    stamp     = buy_val  * cfg.stamp_duty_pct
    charges   = round(brokerage + stt + exch + gst + sebi + stamp, 2)
    net_pnl   = round(gross_pnl - charges, 2)
    return {"gross_pnl": gross_pnl, "charges": charges, "net_pnl": net_pnl}


def main():
    db = SessionLocal()
    try:
        trade = db.get(Trade, TRADE_ID)
        if not trade:
            print(f"ERROR: Trade {TRADE_ID} not found.")
            sys.exit(1)

        print(f"\nTrade {TRADE_ID}: {trade.position_type} {trade.strike} "
              f"| entry ₹{trade.entry_prem} exit ₹{trade.exit_prem} "
              f"| net P&L ₹{trade.net_pnl}")
        print(f"Entry time: {trade.entry_time}  |  Date: {trade.date}")

        opt_type   = "CE" if trade.position_type == "CALL" else "PE"
        min_expiry = OptionsMath.get_expiry_date(TRADE_DATE)
        print(f"\nResolved expiry: {min_expiry}  ({opt_type})")

        # ── Connect Kite (try env/file first, then DB encrypted token) ─────────
        broker = KiteBroker(cfg)
        restored = broker.restore_session()
        if not restored:
            print("No env/file session — trying DB encrypted token...")
            restored = broker.restore_from_db(db, user_id=trade.user_id)
        if not restored:
            print("ERROR: No valid Kite session found.")
            print("  Options:")
            print("  1. Set KITE_ACCESS_TOKEN env var")
            print("  2. Make sure today's token is stored in the DB (log in via the dashboard)")
            sys.exit(1)
        print("Kite session restored.")

        # ── Fetch 1-min option history ────────────────────────────────────────
        records, contract_info = broker.get_option_history(
            trade.strike, opt_type, TRADE_DATE,
            interval="minute", min_expiry=min_expiry,
        )
        if not records:
            print(f"ERROR: No 1-min data from Kite for "
                  f"NIFTY{trade.strike}{opt_type} on {TRADE_DATE}.")
            sys.exit(1)

        print(f"Fetched {len(records)} 1-min candles "
              f"for {contract_info['tradingsymbol']} on {TRADE_DATE}")

        # ── Build ts → close map ─────────────────────────────────────────────
        opt_price_map: dict = {}
        for r in records:
            r_dt = r["date"]
            if r_dt.tzinfo is None:
                r_dt = r_dt.replace(tzinfo=_IST)
            ts = (int(r_dt.timestamp()) // 60) * 60
            opt_price_map[ts] = r["close"]

        # ── Find price at entry_time ─────────────────────────────────────────
        entry_aware = trade.entry_time.replace(tzinfo=_IST)
        entry_ts    = (int(entry_aware.timestamp()) // 60) * 60

        real_entry = (opt_price_map.get(entry_ts)
                      or opt_price_map.get(entry_ts - 60)
                      or opt_price_map.get(entry_ts + 60))

        if real_entry is None:
            # Show the closest candle to help debug
            closest_ts = min(opt_price_map, key=lambda k: abs(k - entry_ts))
            closest_dt = datetime.datetime.fromtimestamp(closest_ts, tz=_IST)
            print(f"ERROR: No candle within ±1 min of {trade.entry_time} IST.")
            print(f"  Closest candle: {closest_dt}  price ₹{opt_price_map[closest_ts]}")
            sys.exit(1)

        entry_dt_ist = datetime.datetime.fromtimestamp(entry_ts, tz=_IST)
        print(f"\nReal entry price at {entry_dt_ist.strftime('%H:%M')} IST: ₹{real_entry:.2f}")
        print(f"Old entry price (Black-Scholes): ₹{trade.entry_prem:.2f}")

        qty = trade.quantity or cfg.qty
        new = recalculate(real_entry, float(trade.exit_prem), qty)
        old = {"gross_pnl": trade.gross_pnl,
               "charges":   trade.charges,
               "net_pnl":   trade.net_pnl}

        print(f"\n{'':>4}{'OLD':>12}  {'NEW':>12}")
        print(f"{'entry_prem':>12}  ₹{trade.entry_prem:>8.2f}  ₹{real_entry:>8.2f}")
        print(f"{'gross_pnl':>12}  ₹{old['gross_pnl']:>8.2f}  ₹{new['gross_pnl']:>8.2f}")
        print(f"{'charges':>12}  ₹{old['charges']:>8.2f}  ₹{new['charges']:>8.2f}")
        print(f"{'net_pnl':>12}  ₹{old['net_pnl']:>8.2f}  ₹{new['net_pnl']:>8.2f}")

        confirm = input("\nApply these changes to the DB? (y/N): ").strip().lower()
        if confirm != "y":
            print("Aborted.")
            sys.exit(0)

        trade.entry_prem = round(real_entry, 2)
        trade.gross_pnl  = new["gross_pnl"]
        trade.charges    = new["charges"]
        trade.net_pnl    = new["net_pnl"]
        db.commit()
        print(f"\nDone. Trade {TRADE_ID} updated in DB.")
        print(f"Restart the engine (switch mode off/on) to reload the corrected trade summary.")

    except Exception as e:
        db.rollback()
        print(f"ERROR: {e}")
        import traceback; traceback.print_exc()
        sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    main()
