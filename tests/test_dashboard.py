"""Offline integration smoke tests. Uses a temporary DB; never starts trading."""
import os
import tempfile
import unittest
from unittest.mock import patch

_tmp = tempfile.TemporaryDirectory()
os.environ.update(DATABASE_URL='sqlite:///' + _tmp.name + '/test.db',
                  HISTORY_CACHE_PATH=_tmp.name + '/history.sqlite',
                  DEFAULT_USER_EMAIL='', DEFAULT_USER_PASSWORD='',
                  KITE_API_KEY='', KITE_API_SECRET='', APP_MODE='PAPER', RESTORE_TRADING_SESSIONS='0',
                  JWT_SECRET_KEY='test-only-secret-key-at-least-32-characters')
from dashboard.app import create_app


class DashboardSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if getattr(cls, 'app', None):
            return
        cls.app = create_app()
        cls.client = cls.app.test_client()
        cls.headers = []
        for i in range(2):
            r = cls.client.post('/api/auth/register', json={
                'email': f'user{i}@example.test', 'username': f'user{i}',
                'password': 'TestPassword123'})
            assert r.status_code == 201, r.json
            cls.headers.append({'Authorization': 'Bearer ' + r.json['token']})

    def test_pages_and_health(self):
        for url in ['/', '/login', '/profile', '/health']:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)
        self.assertEqual(self.client.get('/health').json['running'], 0)

    def test_authentication_and_permissions(self):
        self.assertEqual(self.client.get('/api/state').status_code, 401)
        self.assertEqual(self.client.post('/api/auth/login', json={
            'email': 'user0@example.test', 'password': 'wrong'}).status_code, 401)
        self.assertEqual(self.client.post('/api/auth/login', json={
            'email': 'user0@example.test', 'password': 'TestPassword123'}).status_code, 200)
        for url in ['/api/strategies', '/api/settings']:
            self.assertEqual(self.client.post(url, json={}, headers=self.headers[1]).status_code, 403)

    def test_dashboard_and_analytics(self):
        for url in ['/api/auth/me', '/api/auth/profile', '/api/state', '/api/settings',
                    '/api/balance', '/api/strategies', '/api/strategies/running',
                    '/api/trades', '/api/analytics/summary', '/api/analytics/equity-curve',
                    '/api/analytics/monthly', '/api/analytics/compare', '/api/analytics/insights']:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url, headers=self.headers[0]).status_code, 200)

    def test_strategy_crud_and_isolation(self):
        h = self.headers[0]
        r = self.client.post('/api/strategies', headers=h, json={
            'name': 'Smoke strategy', 'instrument_type': 'EQUITY', 'symbol': 'RELIANCE',
            'rules': {'qty': 1, 'sl_pct': 1, 'tgt_pct': 2}})
        self.assertEqual(r.status_code, 201)
        url = '/api/strategies/' + str(r.json['strategy']['id'])
        self.assertEqual(self.client.get(url, headers=self.headers[1]).status_code, 404)
        self.assertEqual(self.client.put(url, headers=h, json={'name': 'Updated'}).status_code, 200)
        self.assertEqual(self.client.get(url, headers=h).json['strategy']['name'], 'Updated')
        self.assertEqual(self.client.delete(url, headers=h).status_code, 200)

    def test_monthly_respects_date_filter(self):
        import datetime
        from db.database import SessionLocal
        from db.models import Trade
        with SessionLocal() as db:
            rows = [Trade(user_id=1, date=datetime.date(2026, month, 10),
                          trade_mode='PAPER', net_pnl=pnl)
                    for month, pnl in [(1, 100), (2, 250)]]
            db.add_all(rows)
            db.commit()
            try:
                response = self.client.get('/api/analytics/monthly?from=2026-02-01&to=2026-02-28',
                                           headers=self.headers[0])
                self.assertEqual(response.json['data'], [
                    {'month': '2026-02', 'trades': 1, 'net_pnl': 250, 'win_rate': 100}])
            finally:
                for row in rows:
                    db.delete(row)
                db.commit()

    def test_watchlist_and_profile(self):
        h = self.headers[0]
        with patch('dashboard.screener_routes._get_broker', return_value=None):
            self.assertEqual(self.client.post('/api/screener/watchlist', headers=h,
                                             json={'symbol': 'RELIANCE'}).status_code, 200)
            self.assertEqual(len(self.client.get('/api/screener/watchlist', headers=h).json['data']), 1)
            self.assertEqual(self.client.get('/api/screener/watchlist', headers=self.headers[1]).json['data'], [])
            self.assertEqual(self.client.delete('/api/screener/watchlist/RELIANCE', headers=h).status_code, 200)
        r = self.client.post('/api/auth/profile', headers=h, json={'display_name': 'Smoke Test'})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json['user']['display_name'], 'Smoke Test')


if __name__ == '__main__':
    unittest.main()
