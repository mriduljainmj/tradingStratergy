"""Portfolio live revaluation UI, isolated login and simulated market-data frames only."""
import sys
import json
import threading
import tempfile
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
artifacts = Path(tempfile.mkdtemp(prefix='axiom-portfolio-live-'))
at = datetime.datetime.now(datetime.timezone.utc).timestamp()
snapshot = {'ok':True,'fetched_at':datetime.datetime.fromtimestamp(at, datetime.timezone.utc).isoformat(),
 'holdings':[{'instrument_token':123,'tradingsymbol':'TESTHOLD','exchange':'NSE','product':'CNC','quantity':10,'t1_quantity':2,'average_price':100,'last_price':110,'pnl':120,'day_change_percentage':10}],
 'positions':[{'instrument_token':456,'tradingsymbol':'TESTSHORT','exchange':'NFO','product':'MIS','quantity':-5,'multiplier':1,'average_price':100,'last_price':90,'pnl':50}]}
frame = {'status':'connected','server_time':at+2,'quotes':{},'candles':{},'minute_candles':{}}
try:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width':1440,'height':1000})
        errors=[]
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.route('**/api/portfolio',lambda r:r.fulfill(json=snapshot))
        page.route('**/api/market-stream?scope=portfolio&*',lambda r:r.fulfill(content_type='text/event-stream',body='data: '+json.dumps(frame)+'\n\n'))
        page.goto(base+'/login')
        page.get_by_label('Email address').fill('user0@example.test')
        page.get_by_label('Password',exact=True).fill('TestPassword123')
        page.locator('form button.btn.primary').click()
        page.wait_for_url('**/overview')
        page.goto(base+'/portfolio')
        current = page.locator('ax-stat').filter(has_text='Current value')
        pnl = page.locator('ax-stat').filter(has_text='Total holdings P&L')
        positions = page.locator('ax-stat').filter(has_text='Positions P&L')
        expect(current).to_contain_text('₹1,320')
        expect(pnl).to_contain_text('₹120')
        frame['quotes'] = {'123':{'price':120,'time':at+1},'456':{'price':80,'time':at+1}}
        expect(current).to_contain_text('₹1,440',timeout=10000)
        expect(pnl).to_contain_text('₹240')
        expect(positions).to_contain_text('₹100')
        expect(page.locator('ax-stat[label="Overall portfolio return"]')).to_contain_text('+20%')
        expect(page.locator('tr').filter(has_text='TESTHOLD')).to_contain_text('+20.00% today')
        expect(page.locator('tr').filter(has_text='TESTHOLD')).to_contain_text('+20.00% vs cost')
        expect(page.locator('tr').filter(has_text='TESTSHORT')).to_contain_text('+20.00%')
        expect(page.locator('ax-stat[label="Invested value"]')).to_contain_text('₹1,200')
        expect(page.locator('tr').filter(has_text='TESTHOLD')).to_contain_text('₹120.00')
        expect(page.get_by_text('Live · Kite stream',exact=False)).to_be_visible()
        page.screenshot(path=str(artifacts/'desktop.png'),full_page=True)
        frame['status']='reconnecting'
        expect(page.get_by_text('Kite reconnecting',exact=False)).to_be_visible(timeout=10000)
        expect(current).to_contain_text('₹1,440')
        page.set_viewport_size({'width':390,'height':844})
        page.get_by_label('Color theme').select_option('dark')
        page.wait_for_timeout(350)  # Let the responsive sidebar transition finish.
        page.screenshot(path=str(artifacts/'mobile-dark.png'),full_page=True)
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        snapshot['holdings'][0].update(quantity=5,t1_quantity=0,last_price=125,pnl=125)
        snapshot['fetched_at']=datetime.datetime.fromtimestamp(at+10,datetime.timezone.utc).isoformat()
        page.get_by_role('button',name='Refresh portfolio',exact=True).click()
        expect(current).to_contain_text('₹625')
        expect(pnl).to_contain_text('₹125')
        assert not errors, errors
        browser.close()
    print('PASS: live prices, holding/short P&L, unchanged cost, reconnect retention, snapshot reconciliation and mobile dark layout. '+str(artifacts))
finally:
    server.shutdown()
