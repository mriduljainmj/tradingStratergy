import datetime
import json
import logging
import os
from kiteconnect import KiteConnect

from config.settings import TradingConfig

logger = logging.getLogger(__name__)

_TOKEN_CACHE = ".kite_session.json"


class KiteBroker:
    """Thin wrapper around KiteConnect that handles auth and order placement."""

    def __init__(self, config: TradingConfig):
        self.config = config
        self.kite = KiteConnect(api_key=config.api_key)

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
        """Reuse today's cached access token if it exists — skips re-login."""
        try:
            if not os.path.exists(_TOKEN_CACHE):
                return False
            with open(_TOKEN_CACHE) as f:
                cache = json.load(f)
            if cache.get("date") != str(datetime.date.today()):
                return False
            self.kite.set_access_token(cache["access_token"])
            self.kite.profile()  # validates the token is still alive
            logger.info("Restored session from today's cached token.")
            return True
        except Exception:
            return False

    def _save_token(self, access_token: str):
        with open(_TOKEN_CACHE, "w") as f:
            json.dump({"date": str(datetime.date.today()), "access_token": access_token}, f)

    def login_url(self) -> str:
        return self.kite.login_url()

    def get_ltp(self, symbol: str) -> float:
        quote = self.kite.quote(symbol)
        return quote[symbol]["last_price"]

    def get_historical_data(self, token: int, from_dt: str, to_dt: str, interval: str) -> list:
        return self.kite.historical_data(token, from_dt, to_dt, interval)

    def place_market_order(self, symbol: str, transaction_type: str, quantity: int) -> str:
        """Places a MIS market order on NFO. Returns order_id."""
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
        logger.info(f"Order placed — {transaction_type} {quantity}x {exchange_sym} | order_id={order_id}")
        return order_id
