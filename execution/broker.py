import base64
import datetime
import hashlib
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


# ── Fernet encryption helpers ────────────────────────────────────────────────

def _fernet_key() -> bytes:
    """Derive a stable 32-byte Fernet key from env vars."""
    secret = os.getenv("ENCRYPT_KEY") or os.getenv("JWT_SECRET_KEY") or "orb-default-secret-change-me"
    return base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())


def encrypt_token(plain: str) -> str:
    try:
        from cryptography.fernet import Fernet
        return Fernet(_fernet_key()).encrypt(plain.encode()).decode()
    except ImportError:
        # Fallback: base64 (not secure — install cryptography package)
        return base64.b64encode(plain.encode()).decode()


def decrypt_token(enc: str) -> str:
    try:
        from cryptography.fernet import Fernet
        return Fernet(_fernet_key()).decrypt(enc.encode()).decode()
    except ImportError:
        return base64.b64decode(enc.encode()).decode()
    except Exception as e:
        logger.warning(f"Token decryption failed: {e}")
        return ""


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

    # ── Authentication ─────────────────────────────────────────────────────────

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

    def set_token_direct(self, access_token: str) -> bool:
        """Apply an access token directly (e.g. pasted in the profile page)."""
        try:
            self.kite.set_access_token(access_token)
            self.kite.profile()   # validate immediately
            self._save_token(access_token)
            return True
        except Exception as e:
            logger.warning(f"set_token_direct validation failed: {e}")
            return False

    def login_url(self) -> str:
        return self.kite.login_url()

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
                           interval: str = "minute") -> tuple:
        """
        Fetch real 1-min OHLC candles for a NIFTY option on `trade_date`.
        Returns (records, contract_info) where:
          - records      : list of OHLCV dicts (empty on failure)
          - contract_info: dict with tradingsymbol, expiry, token (or None)

        The contract_info lets the caller verify exactly which contract was fetched
        and display it in the UI — critical for confirming weekly vs monthly expiry.
        """
        contract = self.find_option_contract(strike, option_type, trade_date)
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
