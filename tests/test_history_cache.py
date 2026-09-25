import datetime
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from execution.history_cache import HistoryCache


class HistoryCacheTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'history.sqlite'
        self.cache = HistoryCache(self.path)
        self.rows = [dict(date=datetime.datetime(2020,1,1,9,15,tzinfo=datetime.timezone(datetime.timedelta(hours=5,minutes=30))), open=10,high=12,low=9,close=11)]

    def test_persistence_identity_and_bypass(self):
        fetch=Mock(return_value=self.rows)
        key=[1,'TEST',123,'minute','2020-01-01','2020-01-02']
        end=datetime.date(2020,1,2)
        self.assertFalse(self.cache.load(key,end,fetch)[1])
        rows,hit=HistoryCache(self.path).load(key,end,fetch)
        self.assertTrue(hit)
        self.assertEqual(rows,self.rows)
        fetch.assert_called_once()
        self.assertFalse(self.cache.load([2,*key[1:]],end,fetch)[1])
        self.assertFalse(self.cache.load([*key[:-1],'2020-01-03'],end,fetch)[1])
        self.assertFalse(self.cache.load(key,end,fetch,bypass=True)[1])
        self.assertEqual(fetch.call_count,4)

    def test_current_history_expires_and_errors_are_not_cached(self):
        today=datetime.date(2100,1,1)
        fetch=Mock(return_value=self.rows)
        with patch('execution.history_cache.time.time',return_value=100): self.cache.load(['today'],today,fetch)
        with patch('execution.history_cache.time.time',return_value=109): self.assertTrue(self.cache.load(['today'],today,fetch)[1])
        with patch('execution.history_cache.time.time',return_value=111): self.assertFalse(self.cache.load(['today'],today,fetch)[1])
        failed=Mock(side_effect=RuntimeError('broker offline'))
        for _ in range(2):
            with self.assertRaises(RuntimeError): self.cache.load(['error'],today,failed)
        self.assertEqual(failed.call_count,2)

    def test_empty_cached_and_lru_bounded(self):
        fetch=Mock(return_value=[])
        end=datetime.date(2020,1,1)
        self.cache.load(['empty'],end,fetch)
        self.assertTrue(self.cache.load(['empty'],end,fetch)[1])
        with patch('execution.history_cache.time.time',return_value=100):
            self.cache.load(['old-empty'],end,fetch)
        with patch('execution.history_cache.time.time',return_value=3600):
            self.assertTrue(self.cache.load(['old-empty'],end,fetch)[1])
        tiny=HistoryCache(self.path,max_bytes=350)
        for n in range(10): tiny.load([n],end,lambda:self.rows)
        with tiny.connect() as db:
            self.assertLessEqual(db.execute('SELECT SUM(size) FROM history').fetchone()[0],350)
