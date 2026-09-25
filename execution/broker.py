import base64
import datetime
import hashlib
import logging
import os
import time as _time
from typing import Optional

from kiteconnect import KiteConnect

from config.settings import TradingConfig

logger = logging.getLogger(__name__)

_IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

# Module-level NFO instruments cache (refreshed once per trading day)
_nfo_cache: dict = {"date": None, "data": []}

# Resolved contract cache: (strike, type, on_or_after, day) → contract dict.
# get_option_ltp() is called every second while in a position; without this
# each call linearly scans the full ~100k-row NFO instruments list.
_contract_cache: dict = {}


# ── Fernet encryption helpers ────────────────────────────────────────────────

def _fernet_key() -> bytes:
    """Derive a stable 32-byte Fernet key from env vars."""
    from config.security import app_secret
    secret = os.getenv("ENCRYPT_KEY") or app_secret()
    return base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())


def encrypt_token(plain: str) -> str:
    from cryptography.fernet import Fernet
    return Fernet(_fernet_key()).encrypt(plain.encode()).decode()


def decrypt_token(enc: str) -> str:
    from cryptography.fernet import Fernet, InvalidToken
    try:
        return Fernet(_fernet_key()).decrypt(enc.encode()).decode()
    except (InvalidToken, ValueError):
        logger.warning('Token could not be decrypted. Reconnect Kite to renew it.')
        return ''


def is_kite_auth_error(e: Exception) -> bool:
    """Return True if the exception is a Kite 'Incorrect api_key or access_token' error."""
    msg = str(e).lower()
    return ("incorrect api_key" in msg or "incorrect access_token" in msg
            or "invalid api_key" in msg or "invalid access_token" in msg
            or "TokenException" in str(type(e).__name__))


class KiteBroker:
    """Thin wrapper around KiteConnect that handles auth and order placement."""

    def __init__(self, config: TradingConfig):
        self.config = config
        self.kite = KiteConnect(api_key=config.api_key)
        # Historical data / NFO instruments can be large — increase from default 7 s
        self.kite.reqsession.timeout = 30

    # ── Authentication ─────────────────────────────────────────────────────────


    def restore_from_db(self, db_session, user_id: int) -> bool:
        """
        Try to restore today's Kite session from the encrypted token saved in the DB.
        Also updates the broker's api_key from the user's stored api_key (profile-first).
        Suitable for serverless environments where the file-system cache is lost on restart.
        Returns True on success.
        """
        try:
            from db.models import User
            user = db_session.get(User, user_id)
            if not user or not user.kite_access_token_enc:
                return False
            today = datetime.datetime.now(tz=_IST).date()
            if user.kite_token_date != today:
                logger.info("DB token is from a previous day — skipping.")
                return False
            plain = decrypt_token(user.kite_access_token_enc)
            if not plain:
                return False

            # ── Use the profile-stored api_key if available ──────────────────
            if user.kite_api_key_stored:
                self.kite = KiteConnect(api_key=user.kite_api_key_stored)
                self.config.api_key = user.kite_api_key_stored
                logger.info(f"Using profile api_key for user {user_id}.")

            self.kite.set_access_token(plain)
            self.kite.profile()   # validate
            logger.info(f"Kite session restored from DB profile for user {user_id}.")
            return True
        except Exception as e:
            logger.warning(f"restore_from_db failed: {e}")
            return False


    # ── Market data ────────────────────────────────────────────────────────────

    def get_funds(self) -> dict:
        """
        Returns available margin and used margin from Kite (equity / NFO segment).
        Format: {"available": float, "used": float, "total": float}

        Kite's margin response has several sub-fields under "available":
          - net            → total available margin (what Kite app shows) ✓
          - available.cash → raw cash only (excludes collateral / payin)  ✗
        We use `net` to match exactly what the Kite app displays.

        NIFTY options are in the equity (NSE F&O) segment — not commodity (MCX).
        """
        try:
            margins   = self.kite.margins(segment="equity")
            # `net` = total available margin as displayed in the Kite app
            available = float(margins.get("net", 0.0))
            # utilised.debits = total blocked margin (span + exposure + premium etc.)
            used      = float(margins.get("utilised", {}).get("debits", 0.0))
            return {
                "available": round(available, 2),
                "used":      round(used, 2),
                "total":     round(available + used, 2),
            }
        except Exception as e:
            logger.warning(f"get_funds failed: {e}")
            return {"available": 0.0, "used": 0.0, "total": 0.0}

    def get_ltp(self, symbol: str) -> float:
        quote = self.kite.quote(symbol)
        return quote[symbol]["last_price"]

    def get_historical_data(self, token: int, from_dt: str, to_dt: str,
                            interval: str, state=None) -> list:
        try:
            return self.kite.historical_data(token, from_dt, to_dt, interval)
        except Exception as e:
            if is_kite_auth_error(e) and state is not None:
                state.kite_auth_error = True
                logger.error(f"Kite auth error in historical_data: {e}")
            raise

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

    def find_option_contract(self, strike: int, option_type: str,
                             on_or_after: datetime.date) -> Optional[dict]:
        """
        Return the full NFO instrument record for the nearest NIFTY option
        expiring on or after `on_or_after`.
        Normalises expiry to datetime.date regardless of what kiteconnect returns,
        so the >= comparison never raises a TypeError.
        Returns None if no matching contract is found.
        """
        today = str(datetime.datetime.now(tz=_IST).date())
        cache_key = (int(strike), option_type, str(on_or_after), today)
        cached = _contract_cache.get(cache_key)
        if cached is not None:
            return cached

        instruments = self.get_nfo_instruments()

        def _as_date(v):
            """Coerce datetime/date/str to datetime.date."""
            if isinstance(v, datetime.datetime):
                return v.date()
            if isinstance(v, datetime.date):
                return v
            try:
                return datetime.date.fromisoformat(str(v)[:10])
            except Exception:
                return None

        candidates = []
        for inst in instruments:
            if inst.get("name") != "NIFTY":
                continue
            if inst.get("instrument_type") != option_type:
                continue
            try:
                if int(inst.get("strike", 0)) != int(strike):
                    continue
            except (TypeError, ValueError):
                continue
            expiry = _as_date(inst.get("expiry"))
            if expiry is None or expiry < on_or_after:
                continue
            candidates.append((expiry, inst))

        if not candidates:
            logger.warning(
                f"No NFO instrument found for NIFTY {strike}{option_type} "
                f"expiring on/after {on_or_after}"
            )
            return None

        candidates.sort(key=lambda x: x[0])   # nearest expiry first
        expiry, chosen = candidates[0]
        logger.info(
            f"Resolved NIFTY{strike}{option_type} → "
            f"{chosen['tradingsymbol']} (expiry {expiry})"
        )
        if len(_contract_cache) > 256:   # bound the cache across long sessions
            _contract_cache.clear()
        _contract_cache[cache_key] = chosen
        return chosen

    def find_option_token(self, strike: int, option_type: str,
                          on_or_after: datetime.date) -> Optional[int]:
        """Return the instrument token for the nearest-expiry NIFTY option."""
        contract = self.find_option_contract(strike, option_type, on_or_after)
        return contract["instrument_token"] if contract else None

    def find_option_tradingsymbol(self, strike: int, option_type: str,
                                  on_or_after: datetime.date) -> Optional[str]:
        """
        Return the exact Kite tradingsymbol for a NIFTY option
        (e.g. 'NIFTY2651423700PE' for weekly, 'NIFTY26MAY23700PE' for monthly).
        This is what must be passed to place_order(); the format differs between
        weekly and monthly contracts and cannot be safely derived without the
        instruments list.
        """
        contract = self.find_option_contract(strike, option_type, on_or_after)
        if not contract:
            return None
        logger.info(
            f"Order symbol: {contract['tradingsymbol']} (expiry {contract.get('expiry')})"
        )
        return contract["tradingsymbol"]

    # ── Real-time option price (paper / live) ──────────────────────────────────

    def get_option_ltp(self, strike: int, option_type: str) -> Optional[float]:
        """
        Fetch the live last-traded price of a NIFTY option from the exchange.
        Uses get_expiry_date() so we skip holiday-truncated near-expiry contracts
        and fetch from the same contract that was traded.
        Returns None if the instrument is not found or the quote fails.
        """
        from core.options_math import OptionsMath
        today      = datetime.datetime.now(tz=_IST).date()
        min_expiry = OptionsMath.get_expiry_date(today)   # skips holiday Tuesdays
        token = self.find_option_token(strike, option_type, min_expiry)
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
                           interval: str = "minute",
                           min_expiry: datetime.date = None) -> tuple:
        """
        Fetch real 1-min OHLC candles for a NIFTY option on `trade_date`.
        Returns (records, contract_info) where:
          - records      : list of OHLCV dicts (empty on failure)
          - contract_info: dict with tradingsymbol, expiry, token (or None)

        min_expiry: override the on_or_after constraint used to find the contract.
        Use this to pin the lookup to the holiday-adjusted expiry (e.g. June 2
        weekly) when fetching data for a different calendar date (e.g. previous
        trading day). Without it, find_option_contract uses trade_date directly
        and may resolve to the wrong contract (e.g. May 26 monthly instead of
        June 2 weekly) which shows completely different price levels.
        """
        effective_expiry = min_expiry if min_expiry else trade_date
        contract = self.find_option_contract(strike, option_type, effective_expiry)
        if not contract:
            return [], None

        token        = contract["instrument_token"]
        tradingsymbol = contract["tradingsymbol"]
        expiry       = contract.get("expiry")

        try:
            records = self.kite.historical_data(
                token,
                f"{trade_date} 09:15:00",
                f"{trade_date} 15:30:00",
                interval,
            )
            logger.info(
                f"Fetched {len(records)} candles for "
                f"{tradingsymbol} (expiry {expiry}) on {trade_date}"
            )
            contract_info = {
                "tradingsymbol": tradingsymbol,
                "expiry":        str(expiry),
                "token":         token,
            }
            return records, contract_info
        except Exception as e:
            logger.warning(
                f"Option history fetch failed for {tradingsymbol} "
                f"on {trade_date}: {e}"
            )
            return [], None

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

    def place_equity_market_order(self, symbol: str, transaction_type: str,
                                  quantity: int) -> str:
        """Place a MIS market order on NSE cash segment. Returns order_id."""
        exchange_sym = symbol.split(":")[1] if ":" in symbol else symbol
        order_id = self.kite.place_order(
            tradingsymbol=exchange_sym,
            exchange=self.kite.EXCHANGE_NSE,
            transaction_type=transaction_type,
            quantity=quantity,
            order_type=self.kite.ORDER_TYPE_MARKET,
            product=self.kite.PRODUCT_MIS,
            variety=self.kite.VARIETY_REGULAR,
        )
        logger.info(f"Equity order — {transaction_type} {quantity}x {exchange_sym} | id={order_id}")
        return order_id

    def place_market_order(self, symbol: str, transaction_type: str,
                           quantity: int) -> str:
        """Place a MIS market order on NFO. Returns order_id."""
        exchange_sym = symbol.split(":")[1] if ":" in symbol else symbol
        instrument = next((i for i in self.get_nfo_instruments() if i.get('tradingsymbol') == exchange_sym), None)
        lot_size = int((instrument or {}).get('lot_size') or 0)
        if lot_size <= 0 or quantity <= 0 or quantity % lot_size:
            raise ValueError(f'Invalid quantity for {exchange_sym}; current broker lot size is {lot_size}. Update lot size in Settings.')
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
