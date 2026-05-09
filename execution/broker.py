import datetime
import json
import logging
import os
import time as _time
from typing import Optional

from kiteconnect import KiteConnect

from config.settings import TradingConfig

logger = logging.getLogger(__name__)

_TOKEN_CACHE = ".kite_session.json"
_IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

# Module-level NFO instruments cache (refreshed once per trading day)
_nfo_cache: dict = {"date": None, "data": []}


class KiteBroker:
    """Thin wrapper around KiteConnect that handles auth and order placement."""

    def __init__(self, config: TradingConfig):
        self.config = config
        self.kite = KiteConnect(api_key=config.api_key)

    # ── Authentication ─────────────────────────────────────────────────────────

    def authenticate(self, request_token: str) -> bool:
        try:
            data = self.kite.generate_session(request_token, api_secret=self.config.api_secret)
            access_token = data["access_token"]
            self.kite.set_access_token(access_token)
            self._save_token(access_token)
            logger.info("Kite authentication successful.")
            return True
        except Exception as e:
            logger.error(f"Kite authentication failed: {e}")
            return False

    def restore_session(self) -> bool:
        """Reuse today's cached access token — checks env var first, then local file."""
        env_token = os.getenv("KITE_ACCESS_TOKEN", "").strip()
        if env_token:
            try:
                self.kite.set_access_token(env_token)
                self.kite.profile()
                logger.info("Session restored from KITE_ACCESS_TOKEN env var.")
                return True
            except Exception as e:
                logger.warning(f"KITE_ACCESS_TOKEN env var is invalid: {e}")

        try:
            if not os.path.exists(_TOKEN_CACHE):
                return False
            with open(_TOKEN_CACHE) as f:
                cache = json.load(f)
            if cache.get("date") != str(datetime.datetime.now(tz=_IST).date()):
                return False
            self.kite.set_access_token(cache["access_token"])
            self.kite.profile()
            logger.info("Restored session from today's cached token.")
            return True
        except Exception:
            return False

    def _save_token(self, access_token: str):
        with open(_TOKEN_CACHE, "w") as f:
            json.dump({"date": str(datetime.datetime.now(tz=_IST).date()),
                       "access_token": access_token}, f)

    def login_url(self) -> str:
        return self.kite.login_url()

    # ── Market data ────────────────────────────────────────────────────────────

    def get_ltp(self, symbol: str) -> float:
        quote = self.kite.quote(symbol)
        return quote[symbol]["last_price"]

    def get_historical_data(self, token: int, from_dt: str, to_dt: str,
                            interval: str) -> list:
        return self.kite.historical_data(token, from_dt, to_dt, interval)

    # ── NFO instrument lookup ──────────────────────────────────────────────────

    def get_nfo_instruments(self) -> list:
        """Return today's NFO instruments list (cached, fetched once per day)."""
        today = str(datetime.datetime.now(tz=_IST).date())
        if _nfo_cache["date"] != today:
            try:
                instruments = self.kite.instruments("NFO")
                _nfo_cache["data"] = instruments
                _nfo_cache["date"] = today
                logger.info(f"NFO instrument list loaded: {len(instruments)} contracts")
            except Exception as e:
                logger.warning(f"Failed to load NFO instruments: {e}")
        return _nfo_cache["data"]

    def find_option_token(self, strike: int, option_type: str,
                          on_or_after: datetime.date) -> Optional[int]:
        """
        Find the instrument token for a NIFTY option with the nearest expiry
        on or after `on_or_after`.  option_type must be 'CE' or 'PE'.
        """
        instruments = self.get_nfo_instruments()
        candidates = [
            inst for inst in instruments
            if (inst.get("name") == "NIFTY"
                and inst.get("instrument_type") == option_type
                and int(inst.get("strike", 0)) == int(strike)
                and inst.get("expiry") >= on_or_after)
        ]
        if not candidates:
            logger.warning(
                f"No NFO instrument found for NIFTY {strike}{option_type} "
                f"expiring on/after {on_or_after}"
            )
            return None
        candidates.sort(key=lambda x: x["expiry"])   # nearest expiry first
        chosen = candidates[0]
        logger.debug(
            f"Resolved NIFTY{strike}{option_type} → "
            f"{chosen['tradingsymbol']} (expiry {chosen['expiry']})"
        )
        return chosen["instrument_token"]

    # ── Real-time option price (paper / live) ──────────────────────────────────

    def get_option_ltp(self, strike: int, option_type: str) -> Optional[float]:
        """
        Fetch the live last-traded price of a NIFTY option from the exchange.
        Returns None if the instrument is not found or the quote fails.
        """
        today = datetime.datetime.now(tz=_IST).date()
        token = self.find_option_token(strike, option_type, today)
        if not token:
            return None
        try:
            quote = self.kite.quote([token])
            return list(quote.values())[0]["last_price"]
        except Exception as e:
            logger.warning(f"Option LTP fetch failed for NIFTY{strike}{option_type}: {e}")
            return None

    # ── Historical option candles (backtest) ───────────────────────────────────

    def get_option_history(self, strike: int, option_type: str,
                           trade_date: datetime.date,
                           interval: str = "minute") -> list:
        """
        Fetch real 1-min OHLC candles for a NIFTY option on `trade_date`.
        Returns an empty list if Kite has no data (contract too old, holiday, etc.).
        """
        token = self.find_option_token(strike, option_type, trade_date)
        if not token:
            return []
        try:
            records = self.kite.historical_data(
                token,
                f"{trade_date} 09:15:00",
                f"{trade_date} 15:30:00",
                interval,
            )
            logger.info(
                f"Fetched {len(records)} real NFO candles for "
                f"NIFTY{strike}{option_type} on {trade_date}"
            )
            return records
        except Exception as e:
            logger.warning(
                f"Option history fetch failed for NIFTY{strike}{option_type} "
                f"on {trade_date}: {e}"
            )
            return []

    # ── Live order fill price ──────────────────────────────────────────────────

    def get_fill_price(self, order_id: str, max_wait: int = 8) -> Optional[float]:
        """
        Poll the order book until `order_id` is COMPLETE, then return the
        average fill price.  Waits up to `max_wait` seconds.
        """
        for attempt in range(max_wait):
            _time.sleep(1)
            try:
                orders = self.kite.orders()
                for o in orders:
                    if (str(o.get("order_id")) == str(order_id)
                            and o.get("status") == "COMPLETE"):
                        price = o.get("average_price")
                        logger.info(
                            f"Order {order_id} filled at ₹{price:.2f} "
                            f"(after {attempt + 1}s)"
                        )
                        return price
            except Exception as e:
                logger.warning(f"Order poll failed (attempt {attempt + 1}): {e}")
        logger.warning(f"Order {order_id} not confirmed filled within {max_wait}s")
        return None

    # ── Order placement ────────────────────────────────────────────────────────

    def place_market_order(self, symbol: str, transaction_type: str,
                           quantity: int) -> str:
        """Place a MIS market order on NFO. Returns order_id."""
        exchange_sym = symbol.split(":")[1] if ":" in symbol else symbol
        order_id = self.kite.place_order(
            tradingsymbol=exchange_sym,
            exchange=self.kite.EXCHANGE_NFO,
            transaction_type=transaction_type,
            quantity=quantity,
            order_type=self.kite.ORDER_TYPE_MARKET,
            product=self.kite.PRODUCT_MIS,
            variety=self.kite.VARIETY_REGULAR,
        )
        logger.info(
            f"Order placed — {transaction_type} {quantity}x {exchange_sym} "
            f"| order_id={order_id}"
        )
        return order_id
