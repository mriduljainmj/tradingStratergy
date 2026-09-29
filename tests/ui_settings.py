"""Exercise edited Angular numeric controls against the real settings API in a temporary DB."""
import sys
import threading
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_dashboard import DashboardSmokeTests
from werkzeug.serving import make_server
from playwright.sync_api import sync_playwright, expect

DashboardSmokeTests.setUpClass()
server = make_server('127.0.0.1', 0, DashboardSmokeTests.app, threaded=True)
threading.Thread(target=server.serve_forever, daemon=True).start()
base = f'http://127.0.0.1:{server.server_port}'
try:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(base + '/login')
        page.get_by_label('Email address').fill('user0@example.test')
        page.get_by_label('Password', exact=True).fill('TestPassword123')
        page.locator('form button.btn.primary').click()
        page.wait_for_url('**/overview')
        page.goto(base + '/settings')
        for mode in ['PAPER', 'LIVE', 'BACKTEST']:
            page.get_by_label('Settings mode').select_option(mode)
            target = page.get_by_label('Target premium points', exact=True)
            expect(target).to_be_enabled()
            target.fill('100')
            page.get_by_label('Fibonacci trailing ratio', exact=True).fill('0.65')
            page.get_by_label('Strike spacing', exact=True).fill('50')
            page.get_by_label('Opening range end', exact=True).fill('09:35')
            page.get_by_label('Last entry time', exact=True).fill('12:00')
            with page.expect_response(lambda r: '/api/settings' in r.url and r.request.method == 'POST') as saved:
                page.get_by_role('button', name='Save ' + mode.lower() + ' settings').click()
            assert saved.value.status == 200, saved.value.text()
            payload = saved.value.request.post_data_json
            assert payload['target_pts'] == 100 and payload['fib_trail'] == 0.65
            assert payload['strike_spacing'] == 50
            assert payload['or_end_time'].startswith('09:35')
            result = DashboardSmokeTests.client.get('/api/settings?mode=' + mode,
                headers=DashboardSmokeTests.headers[0]).json
            assert result['target_pts'] == 100 and result['fib_trail'] == 0.65
        browser.close()
    print('PASS: edited numeric and time fields save in Paper, Live and Backtest modes.')
finally:
    server.shutdown()
