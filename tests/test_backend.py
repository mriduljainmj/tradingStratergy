"""Regression tests for the Python API and order lifecycle; no external orders."""
import datetime
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
import test_dashboard as fixtures


class BackendRegressionTests(unittest.TestCase):
    def test_chart_history_batches_warm_pages_without_extra_broker_calls(self):
        broker = Mock()
        def records(token, start, end, interval, **kwargs):
            stamp = datetime.datetime.fromisoformat(str(start)[:10] + 'T09:15:00+05:30')
            return [dict(date=stamp, open=10, high=12, low=9, close=11)]
        broker.get_historical_data.side_effect = records
        ue = SimpleNamespace(broker=broker, state=SimpleNamespace(kite_auth_error=False))
        url = '/api/chart/history?symbol=BANKNIFTY&interval=day&range=all&to=1985-01-01'
        with patch('dashboard.routes._ue', return_value=ue), patch('dashboard.routes.time.sleep'):
            first = self.client.get(url + '&batch=128', headers=self.h).json
            self.assertEqual(first['pages_loaded'], 1)
            self.assertEqual(broker.get_historical_data.call_count, 1)
            cursor = first['next_to']
            expected = first['data']
            while cursor:
                older = self.client.get(url.replace('1985-01-01', cursor), headers=self.h).json
                expected = older['data'] + expected
                cursor = older['next_to']
            before = broker.get_historical_data.call_count
            warm = self.client.get(url + '&batch=128', headers=self.h).json
            self.assertTrue(warm['complete'])
            self.assertEqual(warm['data'], expected)
            self.assertEqual(warm['pages_loaded'], before)
            self.assertEqual(warm['cached_pages'], before)
            self.assertEqual(warm['history_as_of'], first['history_as_of'])
            self.assertEqual(broker.get_historical_data.call_count, before)
            refreshed = self.client.get(url + '&batch=128&refresh=1', headers=self.h).json
            self.assertTrue(refreshed['complete'])
            self.assertEqual(refreshed['cached_pages'], before - 1)
            self.assertEqual(broker.get_historical_data.call_count, before + 1)
            other_user = self.client.get(url + '&batch=128', headers=fixtures.DashboardSmokeTests.headers[1]).json
            self.assertEqual(other_user['cached_pages'], 0)
            self.assertEqual(other_user['pages_loaded'], 1)
            self.assertEqual(self.client.get(url + '&batch=129', headers=self.h).status_code, 400)

    def test_chart_history_gzip_preserves_payload(self):
        import gzip
        broker = Mock()
        broker.get_historical_data.return_value = [dict(
            date=datetime.datetime(2018, 1, 1, 9, 15) + datetime.timedelta(minutes=i),
            open=10, high=12, low=9, close=11) for i in range(100)]
        ue = SimpleNamespace(broker=broker, state=SimpleNamespace(kite_auth_error=False))
        url = '/api/chart/history?symbol=NIFTY&interval=minute&from=2018-01-01&to=2018-01-02'
        with patch('dashboard.routes._ue', return_value=ue):
            plain = self.client.get(url, headers=self.h)
            packed = self.client.get(url, headers={**self.h, 'Accept-Encoding': 'gzip'})
            self.assertEqual(packed.headers['Content-Encoding'], 'gzip')
            self.assertEqual(json.loads(gzip.decompress(packed.data))['data'], plain.json['data'])
            self.assertLess(len(packed.data), len(plain.data) / 2)

    def test_chart_history_cache_reuse_and_manual_refresh(self):
        broker=Mock()
        broker.get_historical_data.return_value=[dict(date=datetime.datetime(2019,1,1,9,15),open=10,high=12,low=9,close=11)]
        ue=SimpleNamespace(broker=broker, state=SimpleNamespace(kite_auth_error=False))
        url='/api/chart/history?symbol=NIFTY&interval=minute&from=2019-01-01&to=2019-01-02'
        with patch('dashboard.routes._ue',return_value=ue):
            first=self.client.get(url,headers=self.h)
            second=self.client.get(url,headers=self.h)
            self.assertEqual(first.status_code,200)
            self.assertFalse(first.json['cache_hit'])
            self.assertTrue(second.json['cache_hit'])
            self.assertEqual(second.json['token'],256265)
            self.assertEqual(first.json['data'],second.json['data'])
            self.assertEqual(broker.get_historical_data.call_count,1)
            self.assertFalse(self.client.get(url+'&refresh=1',headers=self.h).json['cache_hit'])
            self.assertEqual(broker.get_historical_data.call_count,2)

    def test_option_chart_uses_exact_selected_contract(self):
        broker = Mock()
        expiry = datetime.date.today() + datetime.timedelta(days=30)
        broker.get_nfo_instruments.return_value = [dict(name='NIFTY', tradingsymbol='EXACTCE', instrument_type='CE', strike=25000, expiry=expiry, instrument_token=123)]
        broker.get_historical_data.return_value = [dict(date=datetime.datetime.now(datetime.timezone.utc), open=100, high=110, low=90, close=105)]
        with patch('dashboard.routes._ue', return_value=SimpleNamespace(broker=broker)):
            catalog = self.client.get('/api/option-contracts', headers=self.h)
            self.assertEqual(catalog.json['contracts'][0]['symbol'], 'EXACTCE')
            result = self.client.get('/api/option-chart?symbol=EXACTCE', headers=self.h)
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.json['tradingsymbol'], 'EXACTCE')
            self.assertEqual(result.json['data'][0]['close'], 105)
            self.assertEqual(broker.get_historical_data.call_args.args[0], 123)
            self.assertEqual(self.client.get('/api/option-chart?symbol=MISSING', headers=self.h).status_code, 404)

    @classmethod
    def setUpClass(cls):
        fixtures.DashboardSmokeTests.setUpClass()
        cls.client = fixtures.DashboardSmokeTests.client
        cls.h = fixtures.DashboardSmokeTests.headers[0]

    def test_chart_three_sessions_preserves_all_candles(self):
        zone = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
        records = [dict(date=datetime.datetime(2026, 9, day, 9, minute, tzinfo=zone),
                        open=100, high=105, low=99, close=103, volume=10)
                   for day in (16, 17, 18, 21) for minute in (15, 16, 17)]
        broker = Mock()
        broker.get_historical_data.return_value = list(reversed(records))
        ue = SimpleNamespace(broker=broker, state=SimpleNamespace(kite_auth_error=False))
        with patch('dashboard.routes._ue', return_value=ue):
            result = self.client.get('/api/chart/history?symbol=NIFTY&interval=minute&sessions=3&to=2026-09-21', headers=self.h)
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.json['session_dates'], ['2026-09-17', '2026-09-18', '2026-09-21'])
            self.assertEqual(len(result.json['data']), 9)
            self.assertEqual(result.json['data'][0]['time'], int(records[3]['date'].timestamp()))
            self.assertEqual(broker.get_historical_data.call_args.args[3], 'minute')
            for query in ('sessions=0', 'sessions=abc', 'sessions=3&interval=week'):
                self.assertEqual(self.client.get('/api/chart/history?symbol=NIFTY&'+query, headers=self.h).status_code, 400)

    def test_chart_weekly_aggregates_daily_ohlcv(self):
        broker = Mock()
        broker.get_historical_data.return_value = [
            dict(date=datetime.datetime(2026,9,21),open=111,high=120,low=110,close=119,volume=30),
            dict(date=datetime.datetime(2026,9,18),open=104,high=112,low=103,close=110,volume=20),
            dict(date=datetime.datetime(2026,9,15),open=100,high=106,low=99,close=105,volume=10),
        ]
        ue = SimpleNamespace(broker=broker, state=SimpleNamespace(kite_auth_error=False))
        with patch('dashboard.routes._ue', return_value=ue):
            result = self.client.get('/api/chart/history?symbol=NIFTY&interval=week&from=2026-09-15&to=2026-09-21', headers=self.h)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(broker.get_historical_data.call_args.args[3], 'day')
        self.assertEqual(broker.get_historical_data.call_args.args[1], '2026-09-14 09:15:00')
        self.assertEqual(result.json['data'][0], dict(time=dict(year=2026,month=9,day=14),open=100,high=112,low=99,close=110,volume=30))
        self.assertEqual(len(result.json['data']), 2)

    def test_chart_weekly_and_three_day_preferences(self):
        workspace = self.client.get('/api/workspace/charts', headers=self.h).json['workspace']
        workspace['days'] = 3
        workspace['panes'][0]['interval'] = 'week'
        self.assertEqual(self.client.put('/api/workspace/charts', headers=self.h, json=workspace).status_code, 200)
        self.assertEqual(self.client.get('/api/workspace/charts', headers=self.h).json['workspace'], workspace)

    def test_all_history_pages_continue_across_empty_ranges(self):
        broker = Mock()
        broker.get_historical_data.return_value = []
        ue = SimpleNamespace(broker=broker, state=SimpleNamespace(kite_auth_error=False))
        with patch('dashboard.routes._ue', return_value=ue), patch('dashboard.routes.time.sleep'):
            first = self.client.get('/api/chart/history?symbol=NIFTY&interval=minute&range=all&to=2026-09-21', headers=self.h)
            self.assertEqual(first.status_code, 200)
            self.assertFalse(first.json['complete'])
            self.assertEqual(first.json['next_to'], '2026-08-22')
            self.assertEqual(broker.get_historical_data.call_args.args[1], '2026-08-23 09:15:00')
            last = self.client.get('/api/chart/history?symbol=NIFTY&interval=day&range=all&to=1970-01-10', headers=self.h)
            self.assertTrue(last.json['complete'])
            self.assertIsNone(last.json['next_to'])
            self.assertEqual(broker.get_historical_data.call_args.args[1], '1970-01-01 09:15:00')
            invalid = self.client.get('/api/chart/history?symbol=NIFTY&range=all&sessions=3', headers=self.h)
            self.assertEqual(invalid.status_code, 400)

    def test_all_weekly_history_pages_have_nonoverlapping_weeks(self):
        broker = Mock()
        broker.get_historical_data.return_value = []
        ue = SimpleNamespace(broker=broker, state=SimpleNamespace(kite_auth_error=False))
        with patch('dashboard.routes._ue', return_value=ue), patch('dashboard.routes.time.sleep'):
            result = self.client.get('/api/chart/history?symbol=NIFTY&interval=week&range=all&to=2026-09-21', headers=self.h)
            start = datetime.date.fromisoformat(broker.get_historical_data.call_args.args[1][:10])
            self.assertEqual(start.weekday(), 0)
            self.assertEqual(result.json['next_to'], str(start - datetime.timedelta(days=1)))
            self.assertEqual(broker.get_historical_data.call_args.args[3], 'day')
        workspace = self.client.get('/api/workspace/charts', headers=self.h).json['workspace']
        workspace['days'] = 'all'
        self.assertEqual(self.client.put('/api/workspace/charts', headers=self.h, json=workspace).status_code, 200)
        self.assertEqual(self.client.get('/api/workspace/charts', headers=self.h).json['workspace']['days'], 'all')

    def test_personal_modes_and_live_authorization(self):
        member = fixtures.DashboardSmokeTests.headers[1]
        for mode in ('BACKTEST', 'PAPER'):
            response = self.client.post('/api/mode', headers=member, json={'mode':mode})
            self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(self.client.post('/api/mode', headers=member, json={'mode':'LIVE'}).status_code, 403)
        self.assertEqual(self.client.post('/api/backtest/run', headers=member,
                                        json={'mode':'single','date':'2026-09-18'}).status_code, 409)
        from core.engine_pool import engine_pool
        ue = engine_pool.get_or_create(2)
        ue.state.app_mode = 'LIVE'
        try:
            response = self.client.post('/api/trades-enabled', headers=member, json={'enabled':True,'mode':'PAPER'})
            self.assertEqual(response.status_code,403)
        finally:
            ue.state.app_mode = 'PAPER'

    def test_select_mode_stays_paused_without_kite(self):
        from core.engine_pool import UserEngine
        ue = UserEngine(900)
        ue.state.kite_auth_error = True
        ue.state.trades_enabled = True
        ue.state.manual_action = 'ENTER_CALL'
        with patch.object(ue,'start') as start, patch('execution.order_safety.unresolved_orders',return_value=False):
            ue.switch_mode('LIVE')
        start.assert_not_called()
        self.assertFalse(ue.state.trades_enabled)
        self.assertTrue(ue.state.kite_auth_error)
        self.assertEqual(ue.state.manual_action,'')

    def test_live_preflight_rejects_unmanaged_exposure(self):
        from execution.order_safety import verify_flat_broker, ExecutionBlocked
        broker = Mock()
        broker.kite.positions.return_value = {'net':[]}
        broker.kite.orders.return_value = []
        verify_flat_broker(broker)
        for positions, orders in (({},[]), ({'net':[{'quantity':1}]},[]),
                                  ({'net':[]},[{'status':'OPEN'}]), ({'net':[{}]},[])):
            broker.kite.positions.return_value = positions
            broker.kite.orders.return_value = orders
            with self.assertRaises(ExecutionBlocked): verify_flat_broker(broker)
        broker.kite.place_order.assert_not_called()

    def test_live_backfill_builds_range_without_simulated_orders(self):
        from execution.trading_engine import TradingEngine
        from config.settings import TradingConfig
        from core.state import BotState
        tz = datetime.timezone(datetime.timedelta(hours=5,minutes=30))
        now = datetime.datetime(2026,9,23,10,0,tzinfo=tz)
        broker = Mock()
        opening = [dict(date=now.replace(hour=9,minute=m),open=100,high=110,low=90,close=100)
                   for m in range(15,35)]
        broker.get_historical_data.return_value = opening + [dict(date=now,open=200,high=210,low=190,close=200)]
        engine = TradingEngine(TradingConfig(), BotState(), broker, user_id=1)
        with patch('execution.trading_engine._now',return_value=now), patch.object(engine,'_handle_signal') as handle:
            engine._backfill_session(real_money=True)
            engine._backfill_session(real_money=False)
        handle.assert_not_called()
        self.assertFalse(engine.strategy.in_position)
        self.assertFalse(engine.strategy.has_traded)
        self.assertEqual(engine.state.or_high,110)
        broker.get_historical_data.return_value = opening[1:]
        with patch('execution.trading_engine._now',return_value=now):
            with self.assertRaises(RuntimeError): engine._backfill_session(real_money=True)
            with self.assertRaises(RuntimeError): engine._backfill_session(real_money=False)

    def test_execution_events_only_for_successful_fills(self):
        from execution.trading_engine import TradingEngine
        from config.settings import TradingConfig
        from core.state import BotState
        engine = TradingEngine(TradingConfig(), BotState(entry_prem=100, exit_prem=110, option_label='PAPER CONTRACT'), Mock(), user_id=1)
        engine._live_symbol = 'CONFIRMED CONTRACT'
        engine._live_quantity = 65
        for live in (False, True):
            for action in ('BUY', 'SELL'):
                with patch.object(engine, '_execute_signal', return_value=None):
                    engine._handle_signal({'action': action, 'reason': 'Test fill'}, real_money=live)
                event = engine.state.to_dict()['execution_events'][-1]
                self.assertEqual(event['mode'], 'LIVE' if live else 'PAPER')
                self.assertEqual(event['price'], 100 if action == 'BUY' else 110)
                self.assertEqual(event['symbol'], 'CONFIRMED CONTRACT' if live else 'PAPER CONTRACT')
                self.assertEqual(event['quantity'], 65 if live else engine.config.qty)
        self.assertEqual(len({e['id'] for e in engine.state.execution_events}), 4)
        with patch.object(engine, '_execute_signal', return_value=False):
            engine._handle_signal({'action': 'BUY'})
        with patch.object(engine, '_execute_signal', side_effect=RuntimeError('Unconfirmed')):
            engine._handle_signal({'action': 'BUY'}, real_money=True)
        self.assertEqual(len(engine.state.execution_events), 4)

    def test_execution_popup_preference_is_saved(self):
        for enabled in (False, True):
            response = self.client.post('/api/auth/profile', headers=self.h, json={'trade_confirm_modal': enabled})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json['user']['trade_confirm_modal'], enabled)
            self.assertEqual(self.client.get('/api/auth/me', headers=self.h).json['user']['trade_confirm_modal'], enabled)
        self.assertEqual(self.client.post('/api/auth/profile', headers=self.h, json={'trade_confirm_modal': 'false'}).status_code, 400)

    def test_blocked_entries_do_not_emit_execution_events(self):
        from execution.trading_engine import TradingEngine
        from config.settings import TradingConfig
        from core.state import BotState
        engine = TradingEngine(TradingConfig(max_daily_loss=100), BotState(), Mock(), user_id=1)
        signal = {'action':'BUY','type':'CALL','price':100,'risk':20,'target':120}
        with patch.object(engine, '_todays_realized_pnl', return_value=-100), patch('execution.trading_engine.send_trade_alert'):
            self.assertFalse(engine._handle_signal(signal))
        with patch.object(engine, '_todays_realized_pnl', return_value=0), patch.object(engine, '_check_balance', return_value=False):
            self.assertFalse(engine._handle_signal(signal, real_money=True))
        self.assertEqual(engine.state.execution_events, [])
        engine.broker.place_market_order.assert_not_called()

    def test_live_orders_validate_broker_lot_size(self):
        from execution.broker import KiteBroker
        from config.settings import TradingConfig
        broker = KiteBroker(TradingConfig())
        broker.kite = Mock()
        with patch.object(broker,'get_nfo_instruments',return_value=[{'tradingsymbol':'TEST','lot_size':65}]):
            with self.assertRaises(ValueError): broker.place_market_order('TEST','BUY',75)
            broker.kite.place_order.assert_not_called()
            broker.place_market_order('TEST','BUY',65)
            broker.kite.place_order.assert_called_once()

    def test_live_exit_uses_confirmed_entry_contract_and_quantity(self):
        from execution.trading_engine import TradingEngine
        from config.settings import TradingConfig
        from core.state import BotState
        broker = Mock()
        engine = TradingEngine(TradingConfig(lot_size=75), BotState(entry_prem=100), broker, user_id=1)
        engine._live_symbol = 'BOUGHT_CONTRACT'
        engine._live_quantity = 65
        with patch('execution.order_safety.confirmed_order', return_value=110) as order:
            engine._execute_signal({'action':'SELL','reason':'Test','price':110,'pnl':650}, real_money=True)
        self.assertEqual(order.call_args.args[2], 'BOUGHT_CONTRACT')
        self.assertEqual(order.call_args.args[4], 65)
        self.assertEqual(engine.state.gross_pnl, 650)
        broker.find_option_tradingsymbol.assert_not_called()

    def test_live_targets_never_trigger_from_synthetic_prices(self):
        from core.strategy import ORBStrategy
        from core.state import BotState
        from config.settings import TradingConfig
        state = BotState(app_mode='LIVE',position_type='CALL',entry_prem=100,current_high=100,current_low=100)
        strategy = ORBStrategy(TradingConfig(),state)
        strategy.in_position = True
        strategy.strike = 100
        strategy.target_prem = 150
        with patch('core.strategy.OptionsMath.bs_call',return_value=200):
            self.assertIsNone(strategy._manage_position(1700000000,datetime.time(10),100,100,100,100))
            result = strategy._manage_position(1700000060,datetime.time(10,1),100,100,100,100,200)
            self.assertEqual(result['reason'],'Target Hit')

    def test_dashboard_state_after_engine_creation_and_execution_block(self):
        from core.engine_pool import UserEngine
        from execution.trading_engine import TradingEngine
        ue = UserEngine(1)
        ue._engine = TradingEngine(ue.config, ue.state, ue.broker, user_id=1)
        with patch('dashboard.routes._ue', return_value=ue):
            response = self.client.get('/api/state', headers=self.h)
            self.assertEqual(response.status_code, 200)
            self.assertFalse(response.json['execution_blocked'])
            self.assertFalse(response.json['in_position'])
            ue._engine.execution_blocked = True
            response = self.client.get('/api/state', headers=self.h)
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json['execution_blocked'])

    def test_portfolio_reads_only_current_users_broker(self):
        broker = Mock()
        broker.kite.holdings.return_value = [dict(tradingsymbol='TEST',exchange='NSE',product='CNC',quantity=10,t1_quantity=2,used_quantity=1,average_price=100,last_price=110,pnl=120,private_value='omit')]
        broker.kite.holdings.return_value[0]['mtf'] = dict(quantity=3, average_price=90, private_value='omit')
        broker.kite.positions.return_value = {'net':[dict(tradingsymbol='SHORT',quantity=-5,pnl=25)],'day':[]}
        ue = SimpleNamespace(user_id=2,broker=broker,state=SimpleNamespace(kite_auth_error=False))
        with patch('dashboard.portfolio_routes.engine_pool.get_or_create',return_value=ue) as get_engine:
            response = self.client.get('/api/portfolio',headers=fixtures.DashboardSmokeTests.headers[1])
        self.assertEqual(response.status_code,200)
        get_engine.assert_called_once_with(2)
        row = response.json['holdings'][0]
        self.assertEqual((row['quantity'],row['t1_quantity'],row['used_quantity']),(10,2,1))
        self.assertNotIn('private_value',row)
        self.assertEqual(row['mtf'], {'quantity':3, 'average_price':90})
        self.assertEqual(response.json['positions'][0]['quantity'],-5)
        broker.kite.place_order.assert_not_called()
        self.assertEqual(self.client.get('/api/portfolio').status_code,401)

    def test_portfolio_errors_are_not_empty_portfolios(self):
        broker = Mock()
        ue = SimpleNamespace(user_id=2,broker=broker,state=SimpleNamespace(kite_auth_error=False))
        with patch('dashboard.portfolio_routes.engine_pool.get_or_create',return_value=ue):
            broker.kite.holdings.return_value = []
            broker.kite.positions.return_value = {'net':[]}
            self.assertEqual(self.client.get('/api/portfolio',headers=self.h).json['holdings'],[])
            broker.kite.positions.return_value = {}
            self.assertEqual(self.client.get('/api/portfolio',headers=self.h).status_code,502)
            broker.kite.holdings.side_effect = Exception('Incorrect api_key or access_token')
            self.assertEqual(self.client.get('/api/portfolio',headers=self.h).status_code,409)
            self.assertTrue(ue.state.kite_auth_error)

    def test_invalid_body_types(self):
        for path in ('/api/strategies', '/api/settings', '/api/auth/profile', '/api/mode'):
            for body in ([], 'text', 123, None):
                with self.subTest(path=path, body=body):
                    r = self.client.post(path, data=json.dumps(body), content_type='application/json', headers=self.h)
                    self.assertEqual(r.status_code, 400)
                    self.assertIn('error', r.json)

    def test_bool_must_not_coerce_false_string(self):
        for path in ('/api/trades-enabled', '/api/background-trading'):
            self.assertEqual(self.client.post(path, json={'enabled': 'false'}, headers=self.h).status_code, 400)
            self.assertEqual(self.client.post(path, json={}, headers=self.h).status_code, 400)

    def test_invalid_numeric_and_date_queries(self):
        for query in ('page=abc','page=0','per_page=-1','per_page=1000','from=bad','from=2026-04-01&to=2026-03-01','strategy_id=nope'):
            r = self.client.get('/api/trades?' + query, headers=self.h)
            self.assertEqual(r.status_code, 400, query)

    def test_settings_validate_and_merge(self):
        for body in ({'lot_size': 0}, {'fib_trail': 2}, {'target_pts': 'abc'}, {'lot_size': True},
                     {'or_end_time':'25:00'}, {'entry_end_time':'09:00'}, {'slippage_pct':float('nan')},
                     {'mode':'invalid'}, {'api_secret':'not-a-setting'}):
            self.assertEqual(self.client.post('/api/settings', json=body, headers=self.h).status_code, 400, body)
        self.assertEqual(self.client.post('/api/settings', json={'mode':'BACKTEST','target_pts':150}, headers=self.h).status_code, 200)
        self.assertEqual(self.client.post('/api/settings', json={'mode':'BACKTEST','qty_multiplier':2}, headers=self.h).status_code, 200)
        saved=self.client.get('/api/settings?mode=BACKTEST',headers=self.h).json
        self.assertEqual(saved['target_pts'],150)
        self.assertEqual(saved['qty_multiplier'],2)

    def test_strategy_validation_create_and_update(self):
        invalid=[{'rules':[]},{'rules':{'qty':-1}},{'engine_type':'UNSUPPORTED'},
                 {'rules':{'ema_fast':30,'ema_slow':10}}, {'rules':{'entry':[]}},
                 {'rules':{'entry':{'conditions':[None]}}}]
        for extra in invalid:
            self.assertEqual(self.client.post('/api/strategies',json={'name':'Invalid',**extra},headers=self.h).status_code,400,extra)
        sid=self.client.get('/api/strategies',headers=self.h).json['strategies'][0]['id']
        for rules in ([], {'target_pts':-3}, {'entry':None}):
            self.assertEqual(self.client.put(f'/api/strategies/{sid}',json={'rules':rules},headers=self.h).status_code,400,rules)

    def test_broker_error_does_not_expire_app_session(self):
        for path in ('/api/chart/history?symbol=NIFTY','/api/auth/kite-info'):
            self.assertEqual(self.client.get(path,headers=self.h).status_code,409)
        self.assertEqual(self.client.get('/api/auth/me',headers=self.h).status_code,200)
        self.assertEqual(self.client.post('/api/trades-enabled',headers=self.h,json={'enabled':True}).status_code,409)

    def test_backtest_validation(self):
        bodies=[{'mode':'single','date':'bad'},{'mode':'range','from_date':'2026-04-01','to_date':'2026-01-01'},
                {'mode':'single','date':'2026-01-01','target_pts':-1}]
        for body in bodies:
            self.assertEqual(self.client.post('/api/backtest/run',headers=self.h,json=body).status_code,400)
        for values in ({'targets':['abc']},{'or_times':['99:99']},{'directions':['BAD']},{'targets':[]},{'targets':[1]*21}):
            r=self.client.post('/api/backtest/optimize',headers=self.h,json={'from_date':'2026-01-01','to_date':'2026-01-02',**values})
            self.assertEqual(r.status_code,400,values)

    def test_profit_factor_is_valid_json(self):
        from dashboard.analytics_routes import _compute_summary
        trade=SimpleNamespace(net_pnl=100,gross_pnl=110,charges=10,date=datetime.date(2026,1,1))
        summary=_compute_summary([trade])
        self.assertIsNone(summary['profit_factor'])
        with fixtures.DashboardSmokeTests.app.app_context():
            from flask import current_app
            encoded=current_app.json.dumps({'data':float('inf'),'summary':summary})
            self.assertNotIn('Infinity',encoded)
            self.assertIsNone(json.loads(encoded)['data'])

    def test_logout_revokes_token(self):
        r=self.client.post('/api/auth/login',json={'email':'user0@example.test','password':'TestPassword123'})
        headers={'Authorization':'Bearer '+r.json['token']}
        self.assertEqual(self.client.post('/api/auth/logout',headers=headers).status_code,200)
        self.assertEqual(self.client.get('/api/auth/me',headers=headers).status_code,401)

    def test_oversized_password_and_photo(self):
        r=self.client.post('/api/auth/register',json={'email':'large@example.test','username':'large','password':'x'*73})
        self.assertEqual(r.status_code,400)
        r=self.client.post('/api/auth/profile',headers=self.h,json={'photo_base64':'data:image/svg+xml;base64,eA=='})
        self.assertEqual(r.status_code,400)

    def test_deleted_user_token_rejected(self):
        r=self.client.post('/api/auth/register',json={'email':'delete@example.test','username':'delete','password':'TestPassword123'})
        headers={'Authorization':'Bearer '+r.json['token']}
        self.assertEqual(self.client.delete('/api/auth/account',headers=headers).status_code,200)
        self.assertEqual(self.client.get('/api/state',headers=headers).status_code,401)

    def test_spa_routes_and_api_404(self):
        for path in ('/overview','/markets','/strategies','/backtests','/results','/charts','/settings','/profile'):
            r=self.client.get(path)
            self.assertEqual(r.status_code,200)
            self.assertIn(b'<app-root>',r.data)
        r=self.client.get('/api/missing-endpoint',headers=self.h)
        self.assertEqual(r.status_code,404)
        self.assertTrue(r.is_json)

    def test_missing_frontend_build_reports_setup_instructions(self):
        from pathlib import Path
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            with patch('dashboard.frontend.FRONTEND', Path(directory)):
                for path in ('/', '/login', '/profile', '/overview'):
                    with self.client.get(path) as response:
                        self.assertEqual(response.status_code, 503)
                        self.assertIn(b'npm run build', response.data)
                self.assertEqual(self.client.get('/health').status_code, 200)

    def test_uncertain_order_is_persisted_and_blocks_resubmission(self):
        from execution.order_safety import confirmed_order, unresolved_orders, ExecutionBlocked
        from db.database import SessionLocal
        from db.models import ExecutionIncident
        broker=Mock();broker.place_market_order.return_value='order-123';broker.get_fill_price.return_value=None
        try:
            with self.assertRaises(ExecutionBlocked):
                confirmed_order(broker,1,'NIFTYTEST','BUY',75)
            self.assertTrue(unresolved_orders(1))
            with self.assertRaises(ExecutionBlocked):
                confirmed_order(broker,1,'NIFTYTEST','BUY',75)
            broker.place_market_order.assert_called_once()
            self.assertEqual(self.client.delete('/api/auth/account',headers=self.h).status_code,409)
        finally:
            with SessionLocal() as db:
                db.query(ExecutionIncident).filter_by(user_id=1).delete();db.commit()

    def test_equity_failed_exit_retains_position_and_no_trade_record(self):
        from execution.equity_engine import EquityEngine
        engine=EquityEngine({'id':1,'name':'Test','symbol':'TEST','rules':{}},Mock(),1)
        engine.paper=False;engine.in_pos=True;engine.entry_px=100
        with patch('execution.order_safety.confirmed_order',side_effect=RuntimeError('rejected')),patch('db.helpers.save_completed_trade') as save:
            engine._exit(110,'Target')
            self.assertTrue(engine.in_pos)
            self.assertFalse(engine.done)
            self.assertTrue(engine.execution_blocked)
            save.assert_not_called()

    def test_options_failed_sell_is_not_success(self):
        from execution.trading_engine import TradingEngine
        from core.state import BotState
        from config.settings import TradingConfig
        engine=TradingEngine(TradingConfig(),BotState(),Mock(),user_id=1)
        with patch.object(engine,'_execute_signal',side_effect=RuntimeError('unconfirmed')):
            self.assertIs(engine._handle_signal({'action':'SELL'},True),False)
            self.assertTrue(engine.strategy.in_position)
            self.assertFalse(engine.state.trades_enabled)
            self.assertEqual(engine.state.net_pnl,0)

    def test_pool_stops_all_engines(self):
        from core.engine_pool import UserEngine
        engine=UserEngine(1)
        engine._engine=Mock();engine._thread=Mock();engine._thread.is_alive.return_value=False
        engine.equity_engines={1:Mock(),2:Mock()}
        engine.stop()
        engine._engine.stop.assert_called_once()
        for equity in engine.equity_engines.values():
            equity.stop.assert_called_once()

    def test_mode_switch_waits_for_previous_worker(self):
        from core.engine_pool import UserEngine
        engine=UserEngine(1)
        engine._engine=Mock();engine._thread=Mock();engine._thread.is_alive.return_value=True
        with patch('execution.order_safety.has_exposure',return_value=False),patch('execution.order_safety.unresolved_orders',return_value=False):
            with self.assertRaises(RuntimeError):
                engine.switch_mode('LIVE')
        self.assertEqual(engine.state.app_mode,'PAPER')

    def test_saved_live_equity_strategy_never_auto_restarts(self):
        from core.engine_pool import UserEngine
        from db.database import SessionLocal
        from db.models import Strategy
        with SessionLocal() as db:
            strategy=Strategy(user_id=1,name='Live restart test',instrument_type='EQUITY',symbol='TEST',engine_type='EQUITY_ORB',is_running=True,run_mode='LIVE',rules='{}')
            db.add(strategy);db.commit();sid=strategy.id
        try:
            engine=UserEngine(1)
            with patch.object(engine,'start_equity_strategy') as start:
                engine.restore_running_equity_strategies()
                start.assert_not_called()
            with SessionLocal() as db:
                self.assertFalse(db.get(Strategy,sid).is_running)
        finally:
            with SessionLocal() as db:
                db.delete(db.get(Strategy,sid));db.commit()

    def test_kite_connect_clears_idle_login_message(self):
        from dashboard.auth_routes import _apply_token_to_broker
        from core.engine_pool import UserEngine
        engine=UserEngine(1)
        engine.state.status='Kite login needed — session restore disabled'
        client=Mock()
        client.profile.return_value={}
        with patch('core.engine_pool.engine_pool.get_or_create',return_value=engine),patch('kiteconnect.KiteConnect',return_value=client):
            _apply_token_to_broker(1,'test-key','test-token')
        self.assertFalse(engine.state.kite_auth_error)
        self.assertFalse(engine.state.trades_enabled)
        self.assertFalse(engine.is_running)
        self.assertEqual(engine.state.status,'Kite connected — trading paused')

    def test_chart_workspace_persistence_and_isolation(self):
        first=self.client.get('/api/workspace/charts',headers=self.h)
        self.assertEqual(first.status_code,200)
        workspace=first.json['workspace']
        workspace.update(count=4,days=30,arrangement='stack')
        workspace['panes'][0]['symbol']='RELIANCE'
        self.assertEqual(self.client.put('/api/workspace/charts',headers=self.h,json=workspace).status_code,200)
        self.assertEqual(self.client.get('/api/workspace/charts',headers=self.h).json['workspace'],workspace)
        other=self.client.get('/api/workspace/charts',headers=fixtures.DashboardSmokeTests.headers[1]).json
        self.assertFalse(other['saved'])
        self.assertEqual(self.client.put('/api/workspace/charts',headers=self.h,json={**workspace,'days':-1}).status_code,400)
        self.assertEqual(self.client.put('/api/workspace/charts',headers=self.h,json={**workspace,'count':True}).status_code,400)

    def test_instrument_company_search_cached_in_db(self):
        from db.models import InstrumentCatalog
        from db.database import SessionLocal
        from core.engine_pool import engine_pool
        ue=engine_pool.get_or_create(1)
        with SessionLocal() as db:
            db.query(InstrumentCatalog).delete();db.commit()
        with patch.object(ue.broker.kite,'instruments',return_value=[{'tradingsymbol':'RELIANCE','name':'Reliance Industries Limited','instrument_type':'EQ','instrument_token':123}]) as fetch:
            response=self.client.get('/api/symbols/search?q=industries&equity=1',headers=self.h)
            self.assertEqual(response.json['results'][0]['symbol'],'RELIANCE')
            self.client.get('/api/symbols/search?q=rel',headers=self.h)
            fetch.assert_called_once()
        self.assertEqual(self.client.get('/api/symbols/search?limit=oops',headers=self.h).status_code,400)

    def test_paper_capital_is_loaded_from_saved_settings(self):
        from core.engine_pool import EnginePool
        response=self.client.post('/api/settings',json={'mode':'PAPER','paper_starting_balance':250000},headers=self.h)
        self.assertEqual(response.status_code,200)
        pool=EnginePool()
        self.assertEqual(pool.get_or_create(1).state.balance,250000)
