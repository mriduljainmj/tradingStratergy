"""Backtest result presentation with simulated historical responses; no broker access."""
import sys
import threading
import datetime
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_dashboard import DashboardSmokeTests
from werkzeug.serving import make_server
from playwright.sync_api import sync_playwright, expect

DashboardSmokeTests.setUpClass()
server = make_server('127.0.0.1', 0, DashboardSmokeTests.app, threaded=True)
threading.Thread(target=server.serve_forever, daemon=True).start()
base = f'http://127.0.0.1:{server.server_port}'
stamp = int(datetime.datetime(2026, 9, 28, 3, 45, tzinfo=datetime.timezone.utc).timestamp())
result = dict(date='2026-09-28', direction='BOTH', position_type='PUT', trade_taken=True,
    entry_prem=100, exit_prem=120, net_pnl=1000, or_high=101, or_low=98,
    candles=[dict(time=stamp+i*300, open=100, high=101, low=98, close=99) for i in range(12)],
    markers=[dict(time=stamp+1260, position='aboveBar', color='#f0798a', shape='arrowDown', text='BUY PUT'),
             dict(time=stamp+2460, position='belowBar', color='#55d9b0', shape='arrowUp', text='EXIT')])
try:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.route('**/api/backtest/run', lambda r: r.fulfill(json=result))
        page.goto(base+'/login')
        page.get_by_label('Email address').fill('user0@example.test')
        page.get_by_label('Password', exact=True).fill('TestPassword123')
        page.locator('form button.btn.primary').click()
        page.wait_for_url('**/overview')
        page.goto(base+'/backtests')
        page.get_by_label('Target points', exact=True).fill('80')
        page.get_by_label('Opening range end', exact=True).fill('09:35')
        for direction in ['PUT', 'CALL']:
            result['position_type'] = direction
            page.get_by_role('button', name='Run backtest').click()
            expect(page.locator('tbody')).to_contain_text('BUY ' + direction)
            expect(page.locator('tbody')).to_contain_text('09:36 IST')
            expect(page.locator('tbody')).to_contain_text('09:56 IST')
            expect(page.locator('ax-chart canvas').first).to_be_visible()
            page.screenshot(path='/tmp/axiom-backtest-annotations.png', full_page=True)
        result.update(position_type='NONE', trade_taken=False, markers=[])
        page.get_by_role('button', name='Run backtest').click()
        expect(page.locator('tbody')).to_contain_text('No trade')
        assert not errors, errors
        browser.close()
    print('PASS: CALL/PUT results, exact trade times, chart annotations and no-trade rendering.')
finally:
    server.shutdown()
