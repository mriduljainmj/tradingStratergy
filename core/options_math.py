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
    def get_atm_strike(ltp: float, strike_spacing: int = 50) -> int:
        return int(round(ltp / strike_spacing) * strike_spacing)
