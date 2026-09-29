"""Offline broker-contract tests. No network or real orders."""
import unittest
import datetime
from types import SimpleNamespace
from unittest.mock import Mock, patch
import test_dashboard as smoke
from db.database import SessionLocal
from db.models import ManualEquityOrder, User, InstrumentCatalog
from kiteconnect.exceptions import InputException


class EquityOrderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        smoke.DashboardSmokeTests.setUpClass()
        cls.client = smoke.DashboardSmokeTests.client
        result = cls.client.post('/api/auth/register', json={
            'email': 'equity@example.test', 'username': 'equity', 'password': 'TestPassword123'})
        cls.headers = {'Authorization': 'Bearer ' + result.json['token']}
        with SessionLocal() as db:
            user = db.query(User).filter_by(username='equity').one()
            cls.uid = user.id
            user.is_admin = True
            db.commit()

    def setUp(self):
        with SessionLocal() as db:
            db.query(ManualEquityOrder).delete()
            db.query(InstrumentCatalog).filter(InstrumentCatalog.exchange.in_(('NSE_CASH', 'BSE_CASH'))).delete()
            db.commit()
        self.kite = Mock(access_token='test-only')
        self.kite.instruments.side_effect = lambda exchange: [dict(tradingsymbol='RELIANCE', name='Reliance', exchange=exchange, segment=exchange, instrument_type='EQ', tick_size=.05, lot_size=1), dict(tradingsymbol='NIFTY', name='Index', exchange=exchange, segment='INDICES', instrument_type='EQ', tick_size=.05, lot_size=1)]
        self.kite.quote.side_effect = lambda keys: {keys[0]: {'last_price': 100, 'timestamp': '2026-09-28 15:00:00'}}
        self.kite.order_margins.return_value = [{'total': 1000}]
        self.kite.holdings.return_value = [{'tradingsymbol':'RELIANCE','quantity':10,'t1_quantity':2}]
        self.kite.positions.return_value = {'net': []}
        self.kite.orders.return_value = []
        self.kite.place_order.return_value = 'order1'
        self.kite.modify_order.return_value = 'order1'
        self.kite.cancel_order.return_value = 'order1'
        self.ue = SimpleNamespace(user_id=self.uid, state=SimpleNamespace(app_mode='LIVE', kite_auth_error=False),
                                  broker=SimpleNamespace(kite=self.kite), is_running=False, equity_engines={})
        self.patch = patch('dashboard.equity_orders.engine_pool.get_or_create', return_value=self.ue)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.order = dict(exchange='NSE', tradingsymbol='RELIANCE', transaction_type='BUY', product='CNC',
                          order_type='LIMIT', variety='regular', quantity=2, price=100, trigger_price=0,
                          disclosed_quantity=0, validity='DAY')

    def preview(self, order=None, **kwargs):
        return self.client.post('/api/equity/preview', headers=self.headers, json={'order':order or self.order, **kwargs})

    def execute(self, review):
        self.assertEqual(review.status_code, 200, review.json)
        return self.client.post('/api/equity/execute', headers=self.headers, json={'review_token':review.json['review_token']})

    def broker_order(self, **kwargs):
        return {**self.order, 'order_id':'order1', 'status':'OPEN', 'filled_quantity':0,'pending_quantity':2, **kwargs}

    def test_orders_deep_link(self):
        self.assertEqual(self.client.get('/orders').status_code, 200)

    def test_search_cash_only_and_bse(self):
        r = self.client.get('/api/equity/instruments?exchange=BSE&q=reli', headers=self.headers)
        self.assertEqual([x['tradingsymbol'] for x in r.json['instruments']], ['RELIANCE'])
        self.assertEqual(r.json['instruments'][0]['exchange'], 'BSE')
        self.assertEqual(self.client.get('/api/equity/instruments?exchange=NFO&q=reli', headers=self.headers).status_code, 400)

    def test_review_and_submit_each_order_type(self):
        for kind in ('MARKET', 'LIMIT', 'SL', 'SL-M'):
            self.order.update(order_type=kind, trigger_price=99)
            review = self.preview()
            self.kite.place_order.reset_mock()
            response = self.execute(review)
            self.assertEqual(response.json['state'], 'submitted', response.json)
            sent = self.kite.place_order.call_args.kwargs
            self.assertEqual(sent['price'], 100 if kind in ('LIMIT','SL') else 0)
            self.assertEqual(sent['trigger_price'], 99 if kind in ('SL','SL-M') else 0)
            self.assertLessEqual(len(sent['tag']), 20)

    def test_idempotent_even_after_mode_changes(self):
        review = self.preview()
        first = self.execute(review)
        self.ue.state.app_mode = 'PAPER'
        second = self.execute(review)
        self.assertEqual(first.json, second.json)
        self.kite.place_order.assert_called_once()

    def test_permissions_mode_connection_and_strategy_guards(self):
        self.assertEqual(self.client.post('/api/equity/preview', json={}).status_code, 401)
        self.assertEqual(self.client.post('/api/equity/preview', headers=smoke.DashboardSmokeTests.headers[1], json={}).status_code, 403)
        self.ue.state.app_mode = 'PAPER'
        self.assertEqual(self.preview().status_code, 409)
        self.ue.state.app_mode = 'LIVE'
        self.ue.is_running = True
        self.assertEqual(self.preview().status_code, 200)
        self.ue.is_running = False
        self.ue.equity_engines = {1:SimpleNamespace(in_pos=False, running=True, paper=False, symbol='RELIANCE', name='Equity ORB')}
        self.assertEqual(self.preview().status_code, 409)
        self.ue.equity_engines = {}
        self.kite.access_token = ''
        self.assertEqual(self.preview().status_code, 409)
        self.kite.place_order.assert_not_called()

    def test_options_exposure_and_unrelated_or_paper_equity_do_not_block(self):
        self.ue.is_running = True
        self.ue._engine = SimpleNamespace(strategy=SimpleNamespace(in_position=True), execution_blocked=False)
        for symbol, paper in [('SBIN', False), ('RELIANCE', True)]:
            self.ue.equity_engines = {1: SimpleNamespace(symbol=symbol, paper=paper, running=True, in_pos=True)}
            with self.subTest(symbol=symbol, paper=paper):
                self.assertEqual(self.execute(self.preview()).json['state'], 'submitted')

    def test_same_symbol_conflict_rechecked_before_submission(self):
        review = self.preview()
        engine = SimpleNamespace(symbol='RELIANCE', paper=False, running=True, in_pos=False, name='Equity ORB')
        self.ue.equity_engines = {1:engine}
        response = self.execute(review)
        self.assertEqual(response.status_code, 409)
        self.assertIn('RELIANCE', response.json['error'])
        self.assertIn('Equity ORB', response.json['error'])
        engine.running, engine.in_pos = False, True
        self.assertEqual(self.preview().status_code, 409)
        self.kite.place_order.assert_not_called()

    def test_strategy_uncertainty_still_blocks_unrelated_orders(self):
        self.ue._engine = SimpleNamespace(execution_blocked=True)
        self.assertEqual(self.preview().status_code, 409)
        self.ue._engine = None
        self.ue.equity_engines = {1:SimpleNamespace(symbol='SBIN', paper=False, running=False, in_pos=False, execution_blocked=True)}
        self.assertEqual(self.preview().status_code, 409)

    def test_managed_stock_modify_and_cancel_blocked(self):
        self.kite.orders.return_value = [self.broker_order()]
        self.ue.equity_engines = {1:SimpleNamespace(symbol='RELIANCE', paper=False, running=True, in_pos=False)}
        for action in ('modify', 'cancel'):
            self.assertEqual(self.preview(action=action, order_id='order1').status_code, 409)
        self.kite.modify_order.assert_not_called()
        self.kite.cancel_order.assert_not_called()

    def test_invalid_fields(self):
        for change in ({'quantity':True}, {'quantity':0}, {'quantity':1.5}, {'price':100.01}, {'price':0},
                       {'exchange':'NFO'}, {'tradingsymbol':'NIFTY'}, {'product':'NRML'}, {'order_type':'OTHER'},
                       {'order_type':'SL','trigger_price':101}, {'order_type':'SL-M','trigger_price':0},
                       {'variety':'amo','validity':'IOC'}, {'disclosed_quantity':3}, {'quantity':100,'disclosed_quantity':2},
                       {'order_type':'MARKET','market_protection':0}):
            with self.subTest(change=change):
                self.assertEqual(self.preview({**self.order, **change}).status_code, 400)
        self.kite.place_order.assert_not_called()

    def test_sell_holdings_reserves_pending_orders(self):
        self.order.update(transaction_type='SELL', quantity=12)
        self.assertEqual(self.preview().status_code, 200)
        self.kite.orders.return_value = [self.broker_order(transaction_type='SELL',quantity=3,pending_quantity=3)]
        self.assertEqual(self.preview().status_code, 400)
        self.order['quantity'] = 9
        self.assertEqual(self.preview().status_code, 200)

    def test_unknown_outcome_blocks_and_reconciles_by_tag(self):
        self.kite.place_order.side_effect = TimeoutError()
        review = self.preview()
        r = self.execute(review)
        self.assertEqual(r.json['state'], 'unknown')
        self.assertEqual(self.preview().status_code, 409)
        self.assertEqual(self.execute(review).json['state'], 'unknown')
        self.kite.place_order.assert_called_once()
        tag = self.kite.place_order.call_args.kwargs['tag']
        self.kite.orders.return_value = [self.broker_order(tag=tag)]
        book = self.client.get('/api/equity/orders', headers=self.headers)
        self.assertEqual(book.json['unresolved'], [])
        self.assertEqual(self.preview().status_code, 200)
        self.assertEqual(self.execute(review).json['state'], 'submitted')
        self.kite.place_order.assert_called_once()

    def test_definite_rejection_is_not_unknown(self):
        self.kite.place_order.side_effect = InputException('Insufficient funds')
        r = self.execute(self.preview())
        self.assertEqual(r.json['state'], 'rejected')
        self.assertIn('Insufficient funds', r.json['message'])
        self.assertEqual(self.preview().status_code, 200)

    def test_modify_and_cancel_partial_order(self):
        self.kite.orders.return_value = [self.broker_order(quantity=5,filled_quantity=2,pending_quantity=3)]
        self.order['quantity'] = 4
        review = self.preview(action='modify', order_id='order1')
        self.assertEqual(review.json['filled_quantity'], 2)
        self.assertEqual(self.execute(review).json['state'], 'submitted')
        self.kite.modify_order.assert_called_once()
        self.order['quantity'] = 2
        self.assertEqual(self.preview(action='modify', order_id='order1').status_code, 400)
        cancel = self.preview(action='cancel', order_id='order1')
        self.assertEqual(self.execute(cancel).json['state'], 'submitted')
        self.kite.cancel_order.assert_called_once_with(variety='regular',order_id='order1')

    def test_no_foreign_or_closed_order_mutations(self):
        self.assertEqual(self.preview(action='cancel',order_id='foreign').status_code, 400)
        self.kite.orders.return_value = [self.broker_order(status='COMPLETE')]
        self.assertEqual(self.preview(action='cancel',order_id='order1').status_code, 400)
        self.kite.cancel_order.assert_not_called()

    def test_order_changes_after_review(self):
        self.kite.orders.return_value = [self.broker_order()]
        review = self.preview(action='modify',order_id='order1')
        self.kite.orders.return_value = [self.broker_order(status='COMPLETE')]
        self.assertEqual(self.execute(review).status_code, 400)
        self.kite.modify_order.assert_not_called()

    def test_tampered_and_expired_review(self):
        review = self.preview()
        r = self.client.post('/api/equity/execute',headers=self.headers,json={'review_token':review.json['review_token']+'invalid'})
        self.assertEqual(r.status_code, 400)
        with patch('itsdangerous.timed.TimestampSigner.get_timestamp', return_value=1):
            old = self.preview()
        self.assertEqual(self.execute(old).status_code, 400)
        self.kite.place_order.assert_not_called()

    def test_explicit_reconciliation_requires_cooldown_flat_account_and_same_day(self):
        self.ue.stop = Mock()
        self.ue.state.reset = Mock()
        self.kite.place_order.side_effect = TimeoutError()
        response = self.execute(self.preview())
        request_id = response.json['request_id']
        incidents = self.client.get('/api/execution/incidents', headers=self.headers)
        self.assertTrue(any(r['id'] == 'equity-' + request_id for r in incidents.json['data']))
        self.assertEqual(self.client.post('/api/execution/reconcile', headers=self.headers).status_code, 409)
        with SessionLocal() as db:
            db.get(ManualEquityOrder, request_id).created_at -= datetime.timedelta(minutes=3)
            db.commit()
        self.kite.positions.return_value = {'net': [{'quantity': 1}]}
        self.assertEqual(self.client.post('/api/execution/reconcile', headers=self.headers).status_code, 409)
        self.kite.positions.return_value = {'net': []}
        self.kite.orders.return_value = [self.broker_order()]
        self.assertEqual(self.client.post('/api/execution/reconcile', headers=self.headers).status_code, 409)
        self.kite.orders.return_value = []
        self.kite.access_token = 'different-session'
        self.assertEqual(self.client.post('/api/execution/reconcile', headers=self.headers).status_code, 409)
        self.kite.access_token = 'test-only'
        cleared = self.client.post('/api/execution/reconcile', headers=self.headers)
        self.assertEqual(cleared.status_code, 200, cleared.json)
        self.assertEqual(cleared.json['reconciled'], 1)
        with SessionLocal() as db:
            row = db.get(ManualEquityOrder, request_id)
            self.assertEqual(row.status, 'reconciled')
            self.assertIsNone(row.active_user)
            row.status = 'unknown'
            row.created_at -= datetime.timedelta(days=1)
            db.commit()
        self.assertEqual(self.client.post('/api/execution/reconcile', headers=self.headers).status_code, 409)

    def test_kite_session_change_invalidates_unsubmitted_review(self):
        review = self.preview()
        self.kite.access_token = 'another-test-session'
        self.assertEqual(self.execute(review).status_code, 409)
        self.kite.place_order.assert_not_called()

    def test_signed_review_is_account_bound(self):
        review = self.preview()
        self.ue.user_id += 100
        self.assertEqual(self.execute(review).status_code, 400)
        self.kite.place_order.assert_not_called()

    def test_amo_mis_and_ioc_and_sell_stop_limit(self):
        for changes in ({'variety':'amo'}, {'product':'MIS', 'transaction_type':'SELL'}, {'validity':'IOC'},
                        {'order_type':'SL', 'transaction_type':'SELL', 'trigger_price':101}):
            with self.subTest(changes=changes):
                self.assertEqual(self.execute(self.preview({**self.order, **changes})).json['state'], 'submitted')

    def test_broker_margin_failure_prevents_review(self):
        self.kite.order_margins.side_effect = TimeoutError()
        self.assertEqual(self.preview().status_code, 502)
        self.kite.place_order.assert_not_called()


if __name__ == '__main__':
    unittest.main()
