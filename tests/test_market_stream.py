import datetime
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from execution.market_stream import MarketStream
import test_dashboard as fixtures


class MarketStreamTests(unittest.TestCase):
    def test_ticks_aggregate_without_day_ohlc_or_out_of_order_updates(self):
        with patch('execution.market_stream.KiteTicker'):
            feed = MarketStream('test-key', 'test-token')
        feed.clients = {'chart': {256265}}
        start = datetime.datetime(2026, 9, 25, 10, 0, tzinfo=datetime.timezone(datetime.timedelta(hours=5, minutes=30)))
        def tick(seconds, price, token=256265):
            feed.on_ticks(None, [dict(instrument_token=token, last_price=price, exchange_timestamp=start + datetime.timedelta(seconds=seconds), ohlc={'high':99999})])
        with patch('execution.market_stream.time.time', return_value=start.timestamp()+600):
            tick(1, 100)
            tick(2, 110)
            tick(3, 95)
            tick(2, 500)  # old packet must not overwrite the close
            tick(301, 105)
            tick(302, 500, 123)  # not subscribed
            tick(303, float('nan'))
        bars = feed.snapshot([256265])['candles']['256265']
        self.assertEqual(len(bars), 2)
        self.assertEqual((bars[0]['open'], bars[0]['high'], bars[0]['low'], bars[0]['close']), (100,110,95,95))
        self.assertEqual(bars[1]['close'],105)
        self.assertNotIn(123, feed.quotes)

    def test_closed_market_does_not_create_new_candles(self):
        with patch('execution.market_stream.KiteTicker'):
            feed = MarketStream('test-key', 'test-token')
        feed.clients = {'chart': {1}}
        stamp = datetime.datetime(2026,9,25,16,0,tzinfo=datetime.timezone(datetime.timedelta(hours=5, minutes=30)))
        with patch('execution.market_stream.time.time', return_value=stamp.timestamp()):
            feed.on_ticks(None,[dict(instrument_token=1,last_price=100,exchange_timestamp=stamp)])
        self.assertEqual(feed.snapshot([1])['candles']['1'], [])

    def test_subscription_union_and_unsubscribe(self):
        with patch('execution.market_stream.KiteTicker'), patch('execution.market_stream.dispatch', side_effect=lambda fn: fn()):
            feed = MarketStream('key','token')
            feed.ticker.is_connected.return_value = True
            feed.attach('first', [1,2])
            feed.on_connect(feed.ticker, None)
            feed.attach('second', [1,3])
            self.assertEqual(feed.subscribed, {1,2,3})
            feed.detach('first')
            self.assertEqual(feed.subscribed, {1,3})
            feed.ticker.unsubscribe.assert_called_with([2])
            feed.close()

    def test_stream_auth_token_validation_and_cleanup(self):
        fixtures.DashboardSmokeTests.setUpClass()
        client = fixtures.DashboardSmokeTests.client
        headers = fixtures.DashboardSmokeTests.headers[0]
        self.assertEqual(client.get('/api/market-stream?tokens=256265').status_code,401)
        feed = Mock(closed=False, credentials=('key','token'))
        feed.snapshot.return_value = {'status':'connected','quotes':{},'candles':{}}
        ue = SimpleNamespace(broker=SimpleNamespace(kite=SimpleNamespace(api_key='key',access_token='token'),get_nfo_instruments=lambda: []),state=SimpleNamespace(kite_auth_error=False),_lifecycle_lock=threading.RLock(),_market_stream=feed)
        with patch('dashboard.stream_routes._ue',return_value=ue):
            self.assertEqual(client.get('/api/market-stream?tokens=bad',headers=headers).status_code,400)
            self.assertEqual(client.get('/api/market-stream?tokens=123',headers=headers).status_code,400)
            response = client.get('/api/market-stream?tokens=256265',headers=headers, buffered=False)
            self.assertEqual(response.status_code,200)
            self.assertIn(b'connected', next(response.response))
            response.close()
            feed.detach.assert_called_once()

    def test_registered_stocks_and_indices_stream_together(self):
        from dashboard.stream_routes import register_chart_token
        fixtures.DashboardSmokeTests.setUpClass()
        client=fixtures.DashboardSmokeTests.client
        headers=fixtures.DashboardSmokeTests.headers[0]
        feed=Mock(closed=False,credentials=('key','token'))
        feed.snapshot.return_value={'status':'connected','quotes':{},'candles':{}}
        ue=SimpleNamespace(broker=SimpleNamespace(kite=SimpleNamespace(api_key='key',access_token='token'),get_nfo_instruments=lambda: []),state=SimpleNamespace(kite_auth_error=False),_lifecycle_lock=threading.RLock(),_market_stream=feed)
        register_chart_token(ue,123)
        register_chart_token(ue,456)
        with patch('dashboard.stream_routes._ue',return_value=ue):
            response=client.get('/api/market-stream?tokens=256265,260105,123,456',headers=headers,buffered=False)
            self.assertEqual(response.status_code,200)
            self.assertEqual(feed.attach.call_args.args[1],{256265,260105,123,456})
            response.close()
            self.assertEqual(client.get('/api/market-stream?tokens=999',headers=headers).status_code,400)
            self.assertEqual(client.get('/api/market-stream?tokens='+','.join(str(i) for i in range(1,34)),headers=headers).status_code,400)
