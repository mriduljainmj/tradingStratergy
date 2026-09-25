"""Bounded, persistent chart-only cache. Trading/backtest reads remain unchanged."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
from contextlib import closing

_locks = [threading.Lock() for _ in range(32)]


class HistoryCache:
    def __init__(self, path=None, max_bytes=64 * 1024 * 1024):
        self.path = Path(path or os.getenv('HISTORY_CACHE_PATH') or Path(__file__).resolve().parents[1] / '.market-cache/history.sqlite')
        self.max_bytes = max_bytes
        self.fetched_at = 0

    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(str(self.path), timeout=10)
        db.execute('CREATE TABLE IF NOT EXISTS history (key TEXT PRIMARY KEY, payload TEXT NOT NULL, expires REAL NOT NULL, accessed REAL NOT NULL, size INTEGER NOT NULL)')
        return db

    def get(self, key, now):
        with closing(self.connect()) as db, db:
            row = db.execute('SELECT payload FROM history WHERE key=? AND expires>?', (key, now)).fetchone()
            if not row:
                return None
            db.execute('UPDATE history SET accessed=? WHERE key=?', (now, key))
        payload = json.loads(row[0])
        if not isinstance(payload, dict):
            return None
        rows = payload['records']
        self.fetched_at = payload['fetched_at']
        for record in rows:
            record['date'] = datetime.datetime.fromisoformat(record['date'])
        return rows

    def put(self, key, records, ttl, now):
        payload = json.dumps({'fetched_at': self.fetched_at, 'records': [{**r, 'date': r['date'].isoformat()} for r in records]}, separators=(',', ':'))
        size = len(payload.encode())
        if size > self.max_bytes:
            return
        with closing(self.connect()) as db, db:
            db.execute('DELETE FROM history WHERE expires<=?', (now,))
            db.execute('INSERT OR REPLACE INTO history VALUES (?,?,?,?,?)', (key, payload, now + ttl, now, size))
            total = db.execute('SELECT COALESCE(SUM(size),0) FROM history').fetchone()[0]
            for old_key, old_size in db.execute('SELECT key,size FROM history ORDER BY accessed').fetchall():
                if total <= self.max_bytes:
                    break
                db.execute('DELETE FROM history WHERE key=?', (old_key,))
                total -= old_size

    def load(self, identity, end_date, fetch, bypass=False):
        key = hashlib.sha256(json.dumps(identity, default=str).encode()).hexdigest()
        with _locks[int(key[:2], 16) % len(_locks)]:
            now = time.time()
            if not bypass:
                try:
                    cached = self.get(key, now)
                    if cached is not None:
                        return cached, True
                except (sqlite3.Error, OSError, ValueError, KeyError):
                    pass  # A cache outage must not hide available broker data.
            self.fetched_at = time.time()
            records = fetch()  # Never cache failed broker requests.
            today = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=5, minutes=30))).date()
            ttl = 10 if end_date >= today else 86400
            if not records:
                ttl = min(ttl, 300)
            try:
                self.put(key, records, ttl, time.time())
            except (sqlite3.Error, OSError, ValueError, TypeError, AttributeError):
                pass
            return records, False
