"""
core/ticker.py — KiteTicker WebSocket manager.

Builds 1-minute OHLC candles from live tick streaming data.
Kite's own charts use the same tick feed, so candles built here
match Kite's charts exactly — unlike historical_data which is
LTP-only and may differ for thinly-traded options.

One TickerManager instance lives per UserEngine.
"""

import datetime
import logging
import threading
from collections import defaultdict, deque
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

_IST          = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
_MAX_CANDLES  = 450   # ~7.5 hours of 1M candles — covers a full trading day


class TickerManager:
    """
    Wraps kiteconnect.KiteTicker and builds 1-minute OHLC candles from ticks.

    Usage:
        tm = TickerManager(api_key)
        tm.connect(access_token)
        tm.subscribe([256265, 12345678])   # NIFTY index + option token

        candles = tm.get_candles(12345678)         # all candles for that token
        candles = tm.get_candles(12345678, since_ts=1234567890)  # incremental
    """

    def __init__(self, api_key: str):
        self.api_key     = api_key
        self._ticker     = None
        self._connected  = False
        self._lock       = threading.Lock()

        # token → deque of COMPLETED {time, open, high, low, close} candles
        self._candles: Dict[int, deque] = defaultdict(
            lambda: deque(maxlen=_MAX_CANDLES)
        )
        # token → current PARTIAL (in-progress) candle being built
        self._partial: Dict[int, dict] = {}
        # token → last price (LTP)
        self._ltp: Dict[int, float] = {}
        # tokens currently subscribed
        self._subscriptions: set = set()

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    def connect(self, access_token: str):
        """Connect the WebSocket using the given access token (non-blocking)."""
        self.disconnect()          # close any stale connection first
        if not access_token:
            logger.warning("TickerManager.connect: no access_token provided, skipping.")
            return
        try:
            from kiteconnect import KiteTicker
            ticker = KiteTicker(self.api_key, access_token)
            ticker.on_ticks     = self._on_ticks
            ticker.on_connect   = self._on_connect
            ticker.on_close     = self._on_close
            ticker.on_error     = self._on_error
            ticker.on_reconnect = self._on_reconnect
            ticker.connect(threaded=True)
            self._ticker = ticker
            logger.info("KiteTicker connecting (threaded)…")
        except Exception as e:
            logger.warning(f"KiteTicker connect failed: {e}")

    def disconnect(self):
        """Close the WebSocket connection gracefully."""
        if self._ticker:
            try:
                self._ticker.close()
            except Exception:
                pass
            self._ticker    = None
            self._connected = False
            logger.info("KiteTicker disconnected.")

    @property
    def is_connected(self) -> bool:
        return self._connected

    # ── Subscription ───────────────────────────────────────────────────────────

    def subscribe(self, tokens: List[int]):
        """Subscribe to instrument tokens in LTP mode."""
        if not tokens:
            return
        with self._lock:
            new_tokens = [t for t in tokens if t not in self._subscriptions]
            self._subscriptions.update(tokens)
        if not new_tokens:
            return
        if self._ticker and self._connected:
            try:
                self._ticker.subscribe(new_tokens)
                self._ticker.set_mode(self._ticker.MODE_LTP, new_tokens)
                logger.info(f"KiteTicker subscribed: {new_tokens}")
            except Exception as e:
                logger.warning(f"KiteTicker subscribe error: {e}")
        # else: will subscribe on_connect when the WS connects

    def unsubscribe(self, tokens: List[int]):
        """Unsubscribe from instrument tokens."""
        if not tokens:
            return
        with self._lock:
            self._subscriptions -= set(tokens)
        if self._ticker and self._connected:
            try:
                self._ticker.unsubscribe(tokens)
                logger.info(f"KiteTicker unsubscribed: {tokens}")
            except Exception as e:
                logger.warning(f"KiteTicker unsubscribe error: {e}")

    # ── Data access ────────────────────────────────────────────────────────────

    def get_candles(self, token: int, since_ts: int = 0) -> List[dict]:
        """
        Return 1-minute OHLC candles for a token.

        Includes completed candles + the current partial (in-progress) candle
        appended as the last item so the chart always shows the live minute.

        Args:
            token:    Kite instrument token.
            since_ts: Unix timestamp (seconds). If > 0, only return candles
                      with time >= since_ts (for incremental polling).
        """
        with self._lock:
            completed = list(self._candles[token])
            partial   = self._partial.get(token)

        if since_ts:
            completed = [c for c in completed if c["time"] >= since_ts]

        if partial:
            if not since_ts or partial["time"] >= since_ts:
                # Replace any stale entry with the same timestamp, then append
                completed = [c for c in completed if c["time"] != partial["time"]]
                completed.append(dict(partial))   # copy — caller must not mutate

        return completed

    def get_ltp(self, token: int) -> Optional[float]:
        """Return the last-traded price for a token, or None."""
        return self._ltp.get(token)

    def has_data(self, token: int) -> bool:
        """True if we have at least one candle (complete or partial) for this token."""
        return bool(self._candles[token] or self._partial.get(token))

    def subscribed_tokens(self) -> list:
        with self._lock:
            return list(self._subscriptions)

    # ── KiteTicker callbacks ───────────────────────────────────────────────────

    def _on_connect(self, ws, response):
        self._connected = True
        logger.info("KiteTicker WebSocket connected.")
        with self._lock:
            tokens = list(self._subscriptions)
        if tokens:
            try:
                ws.subscribe(tokens)
                ws.set_mode(ws.MODE_LTP, tokens)
                logger.info(
                    f"KiteTicker re-subscribed {len(tokens)} token(s) on connect."
                )
            except Exception as e:
                logger.warning(f"KiteTicker re-subscribe error: {e}")

    def _on_close(self, ws, code, reason):
        self._connected = False
        logger.info(f"KiteTicker closed — code={code} reason={reason}")

    def _on_error(self, ws, code, reason):
        logger.warning(f"KiteTicker error — code={code} reason={reason}")

    def _on_reconnect(self, ws, attempt, delay):
        logger.info(
            f"KiteTicker reconnecting — attempt={attempt} delay={delay}s"
        )

    def _on_ticks(self, ws, ticks):
        now = datetime.datetime.now(tz=_IST)
        for tick in ticks:
            token = tick.get("instrument_token")
            price = tick.get("last_price")
            if not token or not price:
                continue
            with self._lock:
                self._ltp[token] = price
            self._update_candle(token, float(price), now)

    # ── Candle builder ─────────────────────────────────────────────────────────

    def _update_candle(self, token: int, price: float,
                       now: datetime.datetime) -> None:
        """
        Update the running 1-minute candle for *token* with *price*.

        When a new minute starts the previous partial candle is promoted to
        the completed deque and a fresh partial candle is opened.
        """
        # Start of current minute (truncate seconds + microseconds)
        bucket_dt = now.replace(second=0, microsecond=0)
        bucket_ts = int(bucket_dt.timestamp())

        with self._lock:
            partial = self._partial.get(token)

            if partial is None or partial["time"] != bucket_ts:
                # New minute — finalise the previous partial (if any)
                if partial is not None:
                    self._candles[token].append(dict(partial))
                # Open a new partial candle
                self._partial[token] = {
                    "time":  bucket_ts,
                    "open":  price,
                    "high":  price,
                    "low":   price,
                    "close": price,
                }
            else:
                # Still in the same minute — update OHLC
                partial["high"]  = max(partial["high"],  price)
                partial["low"]   = min(partial["low"],   price)
                partial["close"] = price
