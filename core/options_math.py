import datetime
import math


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
    def get_atm_strike(ltp: float, strike_spacing: int = 100) -> int:
        """Round LTP to nearest strike_spacing (default 100 for Nifty)."""
        return int(round(ltp / strike_spacing) * strike_spacing)

    @staticmethod
    def get_expiry_date(trade_date: datetime.date) -> datetime.date:
        """
        Return the nearest weekly expiry Tuesday on or after trade_date.
        NSE moved NIFTY weekly options expiry from Thursday to Tuesday
        (effective Oct 2024 onwards).
        If trade_date is itself a Tuesday, that IS the expiry.
        """
        days_ahead = (1 - trade_date.weekday()) % 7   # 1 = Tuesday
        return trade_date + datetime.timedelta(days=days_ahead)

    @staticmethod
    def build_nfo_symbol(strike: int, option_type: str,
                         trade_date: datetime.date | None = None) -> str:
        """
        Build the full NFO tradingsymbol for a Nifty option.
        e.g.  build_nfo_symbol(24200, "CE", date(2026, 4, 21))
              → "NFO:NIFTY26APR24200CE"
        """
        if trade_date is None:
            trade_date = datetime.date.today()
        expiry = OptionsMath.get_expiry_date(trade_date)
        day   = f"{expiry.day:02d}"
        month = expiry.strftime("%b").upper()   # APR, MAY …
        return f"NFO:NIFTY{day}{month}{strike}{option_type}"
