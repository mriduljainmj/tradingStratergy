import datetime
import math

# ── NSE Trading Holidays ──────────────────────────────────────────────────────
# Source: NSE official holiday-master API (Capital Markets segment).
# Used to shift the weekly NIFTY expiry (normally Tuesday) to the NEXT
# Tuesday whenever the regular expiry day itself is an exchange holiday.
#
# ⚠ Update this list each December for the coming year once NSE publishes it.
#   Command to refresh:
#     curl "https://www.nseindia.com/api/holiday-master?type=trading" \
#          -H "User-Agent: Mozilla/5.0" -H "Referer: https://www.nseindia.com/"

_NSE_HOLIDAYS: frozenset = frozenset([
    # ── 2026 (sourced from NSE API, May 2026) ────────────────────────────────
    datetime.date(2026,  1, 15),  # Municipal Corporation Election – Maharashtra
    datetime.date(2026,  1, 26),  # Republic Day                       (Mon)
    datetime.date(2026,  2, 15),  # Mahashivratri                      (Sun – mkt closed anyway)
    datetime.date(2026,  3,  3),  # Holi                               (Tue ← expiry affected)
    datetime.date(2026,  3, 21),  # Id-Ul-Fitr (Ramadan Eid)           (Sat)
    datetime.date(2026,  3, 26),  # Shri Ram Navami                    (Thu)
    datetime.date(2026,  3, 31),  # Shri Mahavir Jayanti               (Tue ← expiry affected)
    datetime.date(2026,  4,  3),  # Good Friday                        (Fri)
    datetime.date(2026,  4, 14),  # Dr. Baba Saheb Ambedkar Jayanti    (Tue ← expiry affected)
    datetime.date(2026,  5,  1),  # Maharashtra Day                    (Fri)
    datetime.date(2026,  5, 26),  # Additional exchange closure        (Tue ← expiry affected)
    datetime.date(2026,  5, 28),  # Bakri Id                           (Thu)
    datetime.date(2026,  6, 26),  # Muharram                           (Fri)
    datetime.date(2026,  8, 15),  # Independence Day                   (Sat)
    datetime.date(2026,  9, 14),  # Ganesh Chaturthi                   (Mon)
    datetime.date(2026, 10,  2),  # Mahatma Gandhi Jayanti             (Fri)
    datetime.date(2026, 10, 20),  # Dussehra                           (Tue ← expiry affected)
    datetime.date(2026, 11,  8),  # Diwali Laxmi Pujan                 (Sun)
    datetime.date(2026, 11, 10),  # Diwali – Balipratipada             (Tue ← expiry affected)
    datetime.date(2026, 11, 24),  # Prakash Gurpurb Sri Guru Nanak Dev (Tue ← expiry affected)
    datetime.date(2026, 12, 25),  # Christmas                          (Fri)

    # ── 2025 (best-known list; update if NSE made late additions) ────────────
    datetime.date(2025,  2, 26),  # Mahashivratri                      (Wed)
    datetime.date(2025,  3, 14),  # Holi                               (Fri)
    datetime.date(2025,  4, 10),  # Shri Ram Navami                    (Thu)
    datetime.date(2025,  4, 14),  # Dr. Baba Saheb Ambedkar Jayanti    (Mon)
    datetime.date(2025,  4, 18),  # Good Friday                        (Fri)
    datetime.date(2025,  5,  1),  # Maharashtra Day                    (Thu)
    datetime.date(2025,  8, 15),  # Independence Day                   (Fri)
    datetime.date(2025, 10,  2),  # Mahatma Gandhi Jayanti             (Thu)
    datetime.date(2025, 11,  5),  # Diwali Laxmi Pujan                 (Wed)
    datetime.date(2025, 11, 25),  # Prakash Gurpurb Sri Guru Nanak Dev (Tue ← expiry affected)
    datetime.date(2025, 12, 25),  # Christmas                          (Thu)
])


class OptionsMath:
    @staticmethod
    def _ncdf(x: float) -> float:
        return 0.5 * math.erfc(-x / math.sqrt(2))

    @staticmethod
    def bs_call(S: float, K: float, T: float, r: float, sigma: float) -> float:
        if T <= 0 or sigma <= 0:
            return max(S - K, 0.05)
        d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)
        return max(S * OptionsMath._ncdf(d1) - K * math.exp(-r * T) * OptionsMath._ncdf(d2), 0.05)

    @staticmethod
    def bs_put(S: float, K: float, T: float, r: float, sigma: float) -> float:
        if T <= 0 or sigma <= 0:
            return max(K - S, 0.05)
        d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)
        return max(K * math.exp(-r * T) * OptionsMath._ncdf(-d2) - S * OptionsMath._ncdf(-d1), 0.05)

    @staticmethod
    def implied_vol(market_price: float, S: float, K: float, T: float, r: float,
                    is_call: bool = True,
                    lo: float = 0.01, hi: float = 5.0,
                    tol: float = 0.05, max_iter: int = 200) -> float:
        """
        Back-solve implied volatility from a market option price using bisection.

        Returns annualised sigma (decimal, e.g. 0.12 = 12%).
        tol is in ₹ (option price units) — 5 paise is sufficient accuracy.
        Falls back to the search midpoint on non-convergence.
        Returns lo if market_price is at or below intrinsic value.
        """
        bs        = OptionsMath.bs_call if is_call else OptionsMath.bs_put
        intrinsic = max(S - K, 0.0) if is_call else max(K - S, 0.0)
        if market_price <= intrinsic:
            return lo
        for _ in range(max_iter):
            mid = (lo + hi) / 2.0
            val = bs(S, K, T, r, mid)
            if abs(val - market_price) < tol:
                return mid
            if val < market_price:
                lo = mid
            else:
                hi = mid
        return (lo + hi) / 2.0

    @staticmethod
    def is_trading_day(date: datetime.date) -> bool:
        """Return True if date is a weekday and not an NSE exchange holiday."""
        return date.weekday() < 5 and date not in _NSE_HOLIDAYS

    @staticmethod
    def get_atm_strike(ltp: float, strike_spacing: int = 100) -> int:
        """Round LTP to nearest strike_spacing (default 100 for Nifty)."""
        return int(round(ltp / strike_spacing) * strike_spacing)

    @staticmethod
    def get_expiry_date(trade_date: datetime.date) -> datetime.date:
        """
        Return the nearest weekly NIFTY option expiry date on or after trade_date.

        NSE moved NIFTY weekly options expiry from Thursday → Tuesday
        (effective Oct 2024).  If trade_date is itself a Tuesday it is the expiry.

        Holiday handling: if the computed Tuesday falls on an NSE exchange
        holiday the contract expiry officially moves to the PREVIOUS trading day,
        but for a *fresh trade entry* we prefer the NEXT Tuesday instead so the
        option has adequate DTE and liquidity.  This matches what Kite shows
        as "the current live weekly" when the near-expiry Tuesday is a holiday.

        Example (today = 21 May 2026, Thursday):
            Normal:  next Tuesday = 26 May  ← NSE holiday (May 2026 list)
            Returns: 02 Jun 2026            ← next non-holiday Tuesday
        """
        # Find the next Tuesday >= trade_date (1 = Tuesday in Python weekday)
        days_ahead = (1 - trade_date.weekday()) % 7
        expiry = trade_date + datetime.timedelta(days=days_ahead)

        # Advance by 7 days (next Tuesday) until we land on a non-holiday.
        # Guard loop (max 8 tries = 8 weeks) in case of unusually dense holidays.
        for _ in range(8):
            if expiry not in _NSE_HOLIDAYS:
                break
            expiry += datetime.timedelta(days=7)

        return expiry

    @staticmethod
    def charges_breakdown(entry_prem: float, exit_prem: float, qty: int, cfg) -> tuple:
        """
        Single source of truth for round-trip transaction costs on a long
        options trade.  Used by the strategy, the backtester, and the live
        engine so the three can never drift apart.

        Returns (total_charges, breakdown_dict) — both rounded to 2 dp.
        """
        buy_val  = entry_prem * qty
        sell_val = exit_prem  * qty
        turnover = buy_val + sell_val

        brokerage = cfg.brokerage_per_order * 2
        stt       = sell_val * cfg.stt_pct
        exch      = turnover * cfg.exchange_charges_pct
        gst       = (brokerage + exch) * cfg.gst_pct
        sebi      = turnover * cfg.sebi_charges_pct
        stamp     = buy_val  * cfg.stamp_duty_pct

        total = round(brokerage + stt + exch + gst + sebi + stamp, 2)
        breakdown = {
            f"Brokerage (₹{cfg.brokerage_per_order:g}/order)":      round(brokerage, 2),
            f"STT ({cfg.stt_pct*100:g}% on sell)":      round(stt,       2),
            f"Exchange ({cfg.exchange_charges_pct*100:g}%)":          round(exch,      2),
            f"GST ({cfg.gst_pct*100:g}% on Brk+Exc)":       round(gst,       2),
            f"SEBI (₹{cfg.sebi_charges_pct*10000000:g}/Cr)":              round(sebi,      2),
            f"Stamp Duty ({cfg.stamp_duty_pct*100:g}% on buy)": round(stamp,     2),
        }
        return total, breakdown
