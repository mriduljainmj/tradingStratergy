"""Read-only, per-account Kite quote streams. Never starts trading engines."""
import datetime
import math
import threading
import time

from kiteconnect import KiteTicker
from twisted.internet import reactor

_startup_lock = threading.Lock()
_IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))


def dispatch(callback):
    with _startup_lock:
        if not reactor.running:
            ready = threading.Event()
            reactor.callWhenRunning(ready.set)
            threading.Thread(target=reactor.run, kwargs={'installSignalHandlers': False}, daemon=True).start()
            if not ready.wait(5):
                raise RuntimeError('Streaming service could not start.')
    reactor.callFromThread(callback)


class MarketStream:
    def __init__(self, api_key, access_token):
        self.credentials = (api_key, access_token)
        self.lock = threading.RLock()
        self.clients = {}
        self.quotes = {}
        self.bars = {}
        self.minute_bars = {}
        self.status = 'connecting'
        self.closed = False
        self.started = False
        self.idle_timer = None
        self.ticker = KiteTicker(api_key, access_token, reconnect=True)
        self.ticker.on_ticks = self.on_ticks
        self.ticker.on_connect = self.on_connect
        self.ticker.on_close = lambda *args: self.set_status('reconnecting')
        self.ticker.on_error = lambda *args: self.set_status('unavailable')
        self.ticker.on_reconnect = lambda *args: self.set_status('reconnecting')
        self.ticker.on_noreconnect = lambda *args: self.close()
        self.subscribed = set()

    def set_status(self, status):
        with self.lock:
            self.status = 'closed' if self.closed else status

    def on_connect(self, ws, response):
        with self.lock:
            if self.closed:
                ws.close()
                return
            self.status = 'connected'
            self.subscribed = set()
        self.sync()

    def sync(self):
        # Executed only on the Twisted reactor thread.
        with self.lock:
            if self.closed or not self.ticker.is_connected():
                return
            desired = set().union(*self.clients.values()) if self.clients else set()
            removed, added = self.subscribed - desired, desired - self.subscribed
            if removed:
                self.ticker.unsubscribe(list(removed))
            if added:
                self.ticker.subscribe(list(added))
                self.ticker.set_mode(self.ticker.MODE_FULL, list(added))
            self.subscribed = desired
            self.quotes = {k: v for k, v in self.quotes.items() if k in desired}
            self.bars = {k: v for k, v in self.bars.items() if k in desired}
            self.minute_bars = {k: v for k, v in self.minute_bars.items() if k in desired}

    def attach(self, client, tokens):
        with self.lock:
            if self.closed:
                raise ValueError('Stream closed. Reconnect.')
            if len(self.clients) >= 6:
                raise ValueError('Too many chart sessions. Close another tab and retry.')
            if self.idle_timer:
                self.idle_timer.cancel()
            self.clients[client] = set(tokens)
            if not self.started:
                self.started = True
                dispatch(lambda: self.ticker.connect(threaded=False))
            else:
                dispatch(self.sync)

    def detach(self, client):
        with self.lock:
            self.clients.pop(client, None)
            if not self.clients:
                self.idle_timer = threading.Timer(10, self.close_if_idle)
                self.idle_timer.daemon = True
                self.idle_timer.start()
            dispatch(self.sync)

    def close_if_idle(self):
        with self.lock:
            if not self.clients:
                self.close()

    def close(self):
        with self.lock:
            self.closed = True
            self.status = 'closed'
            dispatch(self.ticker.close)

    def on_ticks(self, ws, ticks):
        now = time.time()
        with self.lock:
            desired = set().union(*self.clients.values()) if self.clients else set()
            for tick in ticks:
                token, price = tick.get('instrument_token'), tick.get('last_price')
                stamp = tick.get('exchange_timestamp')
                if token not in desired or not isinstance(price, (int, float)) or not math.isfinite(price) or price <= 0 or not stamp:
                    continue
                # Kite SDK returns local naive datetime.fromtimestamp; timestamp()
                # preserves the exchange epoch on both UTC and IST hosts.
                ts = stamp.timestamp()
                previous = self.quotes.get(token)
                if ts > now + 5 or (previous and ts < previous['time']):
                    continue
                self.quotes[token] = dict(price=price, time=ts, received=now)
                market_time = datetime.datetime.fromtimestamp(ts, _IST)
                if market_time.weekday() >= 5 or not datetime.time(9, 15) <= market_time.time() < datetime.time(15, 30):
                    continue
                bucket = int(ts // 300) * 300
                bars = self.bars.setdefault(token, {})
                bar = bars.setdefault(bucket, dict(time=bucket, open=price, high=price, low=price, close=price, last_tick=ts))
                bar.update(high=max(bar['high'], price), low=min(bar['low'], price), close=price, last_tick=ts)
                self.bars[token] = dict(sorted(bars.items())[-90:])
                minute = int(ts // 60) * 60
                minutes = self.minute_bars.setdefault(token, {})
                row = minutes.setdefault(minute, dict(time=minute, open=price, high=price, low=price, close=price, last_tick=ts))
                row.update(high=max(row['high'],price), low=min(row['low'],price), close=price,last_tick=ts)
                self.minute_bars[token] = dict(sorted(minutes.items())[-390:])

    def snapshot(self, tokens, tail=None):
        with self.lock:
            def rows(source, token):
                values = [dict(b) for b in source.get(token, {}).values()]
                return values[-tail:] if tail else values
            return dict(status=self.status, server_time=time.time(), quotes={str(t): self.quotes[t] for t in tokens if t in self.quotes},
                        minute_candles={str(t): rows(self.minute_bars,t) for t in tokens},
                        candles={str(t): rows(self.bars,t) for t in tokens})
