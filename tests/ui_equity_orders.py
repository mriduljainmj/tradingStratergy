"""End-to-end equity ticket checks against isolated auth/DB and a mocked Kite client."""
import sys
import threading
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_equity_orders import EquityOrderTests
import test_dashboard as smoke
from werkzeug.serving import make_server
from playwright.sync_api import sync_playwright, expect

EquityOrderTests.setUpClass()
fixture = EquityOrderTests()
fixture.setUp()
server = make_server('127.0.0.1', 0, smoke.DashboardSmokeTests.app, threaded=True)
threading.Thread(target=server.serve_forever, daemon=True).start()
base = f'http://127.0.0.1:{server.server_port}'
artifacts = Path(tempfile.mkdtemp(prefix='axiom-equity-ui-'))
try:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width':1440,'height':1050})
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.route('**/api/state', lambda r: r.fulfill(json={'app_mode':'LIVE','engine_running':False,'execution_events':[]}))
        page.route('**/api/strategies*', lambda r: r.fulfill(json={'ok':True,'strategies':[],'data':[]}))
        page.route('**/api/chart/**', lambda r: r.fulfill(json={'ok':True,'data':[],'complete':True}))
        page.route('**/api/balance', lambda r: r.fulfill(json={'ok':True,'balance':0}))
        page.goto(base+'/login')
        page.get_by_label('Email address').fill('equity@example.test')
        page.get_by_label('Password', exact=True).fill('TestPassword123')
        page.locator('form button.btn.primary').click()
        page.wait_for_url('**/overview')
        page.goto(base+'/orders')
        page.get_by_label('Stock or company').fill('reli')
        page.locator('.suggestions button').filter(has_text='RELIANCE').click()
        page.get_by_label('Order type',exact=True).select_option('LIMIT')
        page.get_by_label('Quantity',exact=True).fill('2')
        page.get_by_label('Limit price (₹)').fill('100')
        page.screenshot(path=str(artifacts/'desktop.png'), full_page=True)
        page.get_by_label('Color theme').select_option('dark')
        page.screenshot(path=str(artifacts/'dark.png'), full_page=True)
        page.get_by_label('Color theme').select_option('light')
        page.get_by_role('button',name='Review buy order').click()
        dialog = page.get_by_role('dialog')
        expect(dialog).to_be_visible()
        expect(dialog).to_contain_text('₹1,000.00')
        page.screenshot(path=str(artifacts/'review.png'))
        dialog.get_by_role('button',name='Confirm buy',exact=True).click()
        expect(dialog).not_to_be_visible()
        expect(page.get_by_role('status').filter(has_text='SUBMITTED')).to_be_visible()
        fixture.kite.place_order.assert_called_once()
        fixture.kite.orders.return_value = [fixture.broker_order()]
        page.get_by_role('button',name='Refresh orders',exact=True).click()
        page.get_by_role('button',name='Modify',exact=True).click()
        page.get_by_label('Quantity',exact=True).fill('3')
        page.get_by_label('Limit price (₹)').fill('99')
        page.get_by_role('button',name='Review changes',exact=True).click()
        dialog.get_by_role('button',name='Confirm buy',exact=True).click()
        expect(dialog).not_to_be_visible()
        fixture.kite.modify_order.assert_called_once()
        page.get_by_role('button',name='Cancel order',exact=True).click()
        dialog.get_by_role('button',name='Confirm cancellation',exact=True).click()
        expect(dialog).not_to_be_visible()
        fixture.kite.cancel_order.assert_called_once()
        page.set_viewport_size({'width':390,'height':844})
        page.goto(base+'/orders?symbol=RELIANCE&exchange=NSE&side=SELL&quantity=5')
        expect(page.get_by_label('Stock or company')).to_have_value('RELIANCE')
        expect(page.get_by_label('Quantity',exact=True)).to_have_value('5')
        expect(page.get_by_role('button',name='Review sell order')).to_be_enabled()
        page.screenshot(path=str(artifacts/'mobile.png'), full_page=True)
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'Mobile overflow'
        page.get_by_role('button',name='Review sell order').click()
        expect(dialog).to_contain_text('SELL')
        page.screenshot(path=str(artifacts/'mobile-review.png'))
        dialog.get_by_role('button',name='Go back',exact=True).click()
        assert not errors, errors
        browser.close()
    print(f'PASS: buy, modify, cancel, holding-sale deep link, mobile layout; mock broker only. Screenshots: {artifacts}')
finally:
    fixture.doCleanups()
    server.shutdown()
