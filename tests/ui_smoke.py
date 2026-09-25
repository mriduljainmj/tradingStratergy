"""Angular browser regression checks against an isolated, offline Flask database.
Build frontend first. Install playwright + Chromium into .venv to run this file.
"""
import sys
import os
import threading
import tempfile
import json
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_dashboard import DashboardSmokeTests
from werkzeug.serving import make_server
from playwright.sync_api import sync_playwright, expect

DashboardSmokeTests.setUpClass()
server = make_server('127.0.0.1', 0, DashboardSmokeTests.app, threaded=True)
threading.Thread(target=server.serve_forever, daemon=True).start()
base = f'http://127.0.0.1:{server.server_port}'
artifacts = Path(tempfile.mkdtemp(prefix='axiom-ui-'))
try:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width':1440,'height':1000})
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        # Public metadata fixture only. All account/strategy/analytics calls use Flask.
        page.route('**/api/screener/all-instruments', lambda r: r.fulfill(json={
            'ok':True,'data':[{'symbol':'RELIANCE','name':'Reliance Industries','sector':'Energy'}]}))
        page.route('**/api/symbols/search*',lambda r:r.fulfill(json={'ok':True,'results':[{'symbol':'RELIANCE','name':'Reliance Industries Limited','exchange':'NSE'}]}))
        page.goto(base+'/login')
        theme = os.environ.get('UI_TEST_THEME', 'light')
        page.get_by_label('Color theme',exact=True).select_option('system')
        page.emulate_media(color_scheme='dark')
        expect(page.locator('html')).to_have_attribute('data-theme','dark')
        page.emulate_media(color_scheme='light')
        expect(page.locator('html')).to_have_attribute('data-theme','light')
        page.get_by_label('Color theme',exact=True).select_option(theme)
        page.reload()
        expect(page.locator('html')).to_have_attribute('data-theme',theme)
        page.screenshot(path=str(artifacts/'login-theme.png'),full_page=True)
        page.get_by_label('Email address').fill('user0@example.test')
        page.get_by_label('Password', exact=True).fill('TestPassword123')
        page.locator('form button.btn.primary').click()
        page.wait_for_url('**/overview')
        expect(page.get_by_text('Your market, in focus')).to_be_visible()
        for width in [1440,768,390,320]:
            page.set_viewport_size({'width':width,'height':1000})
            for path in ['overview','portfolio','markets','strategies','backtests','results','charts','profile','settings']:
                page.goto(base+'/'+path)
                expect(page.locator('h1')).to_be_visible()
                expect(page.locator('html')).to_have_attribute('data-theme',theme)
                page.wait_for_timeout(150)
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), (width,path)
                if width in [1440,390]:
                    page.screenshot(path=str(artifacts/f'{width}-{path}.png'),full_page=True)
        page.set_viewport_size({'width':1440,'height':1000})
        # Execution acknowledgments: baseline history, queueing, deduplication,
        # persisted off preference, and both modes without sending broker orders.
        event = {'id':'old', 'mode':'PAPER', 'action':'BUY', 'symbol':'TEST CONTRACT',
                 'quantity':65, 'price':100, 'reason':'Test entry', 'time':'2026-09-23T10:00:00+05:30'}
        execution_state = {'app_mode':'PAPER','kite_auth_error':True,'engine_running':False,
                           'trades_enabled':False,'candles':[],'logs':[], 'execution_events':[event]}
        page.route('**/api/state',lambda r:r.fulfill(json=execution_state))
        page.goto(base+'/overview')
        toggle = page.get_by_role('switch',name='Trade execution popups',exact=True)
        expect(toggle).to_have_attribute('aria-checked','true')
        expect(page.get_by_role('alertdialog')).to_have_count(0)
        execution_state['execution_events'].append({**event,'id':'paper-entry'})
        expect(page.get_by_role('alertdialog')).to_contain_text('Paper trade executed',timeout=10000)
        expect(page.get_by_role('alertdialog')).to_contain_text('10:00:00 AM IST')
        page.screenshot(path=str(artifacts/'execution-popup.png'),full_page=True)
        page.get_by_role('button',name='Got it',exact=True).click()
        page.wait_for_timeout(4500)
        expect(page.get_by_role('alertdialog')).to_have_count(0)
        toggle.click()
        expect(toggle).to_have_attribute('aria-checked','false')
        page.reload()
        expect(toggle).to_have_attribute('aria-checked','false')
        execution_state['execution_events'].append({**event,'id':'off-fill'})
        page.wait_for_timeout(4500)
        expect(page.get_by_role('alertdialog')).to_have_count(0)
        toggle.click()
        expect(toggle).to_have_attribute('aria-checked','true')
        execution_state['app_mode'] = 'LIVE'
        execution_state['execution_events'].extend([
            {**event,'id':'live-entry','mode':'LIVE'},
            {**event,'id':'live-exit','mode':'LIVE','action':'SELL','reason':'Target hit'}])
        expect(page.get_by_role('alertdialog')).to_contain_text('Live trade executed',timeout=10000)
        expect(page.get_by_role('alertdialog')).to_contain_text('Entry · Buy')
        page.get_by_role('button',name='Got it',exact=True).click()
        expect(page.get_by_role('alertdialog')).to_contain_text('Exit · Sell')
        page.keyboard.press('Escape')
        expect(page.get_by_role('alertdialog')).to_have_count(0)
        expect(toggle).to_have_attribute('aria-checked','true')
        page.unroute('**/api/state')
        page.route('**/api/portfolio',lambda r:r.fulfill(json={'ok':True,'fetched_at':'2026-09-23T04:00:00+00:00','holdings':[{'tradingsymbol':'HOLDINGTEST','exchange':'NSE','product':'CNC','quantity':10,'t1_quantity':2,'used_quantity':0,'average_price':100,'last_price':110,'pnl':120}], 'positions':[{'tradingsymbol':'SHORTTEST','exchange':'NFO','product':'MIS','quantity':-5,'average_price':100,'last_price':90,'pnl':50}]}))
        page.goto(base+'/portfolio')
        expect(page.get_by_text('HOLDINGTEST',exact=True)).to_be_visible()
        expect(page.get_by_text('SHORTTEST',exact=True)).to_be_visible()
        expect(page.locator('ax-stat').filter(has_text='Invested value')).to_contain_text('₹1,200')
        expect(page.locator('ax-stat').filter(has_text='Current value')).to_contain_text('₹1,320')
        expect(page.locator('ax-stat').filter(has_text='Total holdings P&L')).to_contain_text('₹120')
        expect(page.locator('ax-stat').filter(has_text='Positions P&L')).to_contain_text('₹50')
        expect(page.get_by_role('button',name='Refresh portfolio',exact=True)).to_be_enabled()
        page.screenshot(path=str(artifacts/'portfolio-populated.png'),full_page=True)
        page.unroute('**/api/portfolio')
        page.route('**/api/portfolio',lambda r:r.fulfill(status=502,json={'ok':False,'error':'Kite portfolio unavailable'}))
        page.get_by_role('button',name='Refresh portfolio',exact=True).click()
        expect(page.get_by_role('alert')).to_contain_text('Kite portfolio unavailable')
        expect(page.get_by_text('Last successful snapshot',exact=False)).to_be_visible()
        expect(page.get_by_text('HOLDINGTEST',exact=True)).to_be_visible()
        page.unroute('**/api/portfolio')
        page.goto(base+'/strategies')
        page.get_by_role('button',name='Create strategy',exact=True).click()
        page.get_by_label('Strategy name').fill('Browser regression strategy')
        page.get_by_role('button',name='Save strategy',exact=True).click()
        card = page.locator('article').filter(has_text='Browser regression strategy')
        expect(card).to_be_visible()
        card.get_by_role('button',name='Edit',exact=True).click()
        page.get_by_label('Target points',exact=True).fill('145')
        page.get_by_role('button',name='Save strategy',exact=True).click()
        expect(card.get_by_text('145',exact=True)).to_be_visible()
        card.get_by_role('button',name='Delete',exact=True).click()
        page.get_by_role('alertdialog').get_by_role('button',name='Cancel',exact=True).click()
        expect(card).to_be_visible()
        card.get_by_role('button',name='Delete',exact=True).click()
        page.get_by_role('alertdialog').get_by_role('button',name='Delete strategy',exact=True).click()
        expect(card).to_have_count(0)
        page.goto(base+'/profile')
        page.get_by_label('Display name',exact=True).fill('UI Reviewer')
        page.get_by_role('button',name='Save changes',exact=True).click()
        expect(page.locator('.profile-card h2')).to_have_text('UI Reviewer')
        page.goto(base+'/overview')
        page.route('**/api/state',lambda r:r.abort())
        expect(page.get_by_text('Server unavailable',exact=True)).to_be_visible(timeout=10000)
        page.unroute('**/api/state')
        expect(page.get_by_text('Server connected',exact=True)).to_be_visible(timeout=10000)
        # Render populated historical charts without making a broker call.
        candles=[{'time':1726707600+i*300,'open':25000+i,'high':25010+i,'low':24990+i,'close':25005+i} for i in range(20)]
        page.route('**/api/chart/history*',lambda r:r.fulfill(json={'ok':True,'data':candles}))
        page.route('**/api/state',lambda r:r.fulfill(json={'app_mode':'PAPER','kite_auth_error':False,'engine_running':False,'trades_enabled':False,'candles':[{'time':1726600000,'open':100,'high':110,'low':90,'close':99}],'live_nifty_ltp':99,'logs':[],'status':'Kite connected — trading paused'}))
        page.goto(base+'/overview')
        expect(page.get_by_text('5-minute market history · auto-refresh every 30 seconds',exact=False)).to_be_visible()
        expect(page.locator('.main-chart .chart-empty')).to_have_count(0)
        expect(page.locator('.main-chart .chart-readout')).to_contain_text('25,024')
        expect(page.locator('.market-price')).to_contain_text('25,024')
        candles[-1]['close'] = 25030
        page.get_by_role('button',name='Refresh market data',exact=True).click()
        expect(page.locator('.main-chart .chart-readout')).to_contain_text('25,030')
        candles[-1]['close'] = 25031
        expect(page.locator('.main-chart .chart-readout')).to_contain_text('25,031',timeout=35000)
        page.get_by_role('button',name='Fit NIFTY chart data',exact=True).click()
        page.get_by_role('button',name='Show latest NIFTY candles',exact=True).click()
        page.get_by_role('button',name='Focus NIFTY chart',exact=True).click()
        expect(page.locator('.overview-chart-frame:fullscreen')).to_have_count(1)
        expect(page.get_by_role('button',name='Focus NIFTY chart',exact=True)).to_have_text('Restore')
        page.get_by_role('button',name='Focus NIFTY chart',exact=True).click()
        expect(page.locator('.overview-chart-frame:fullscreen')).to_have_count(0)
        page.route('**/api/option-contracts',lambda r:r.fulfill(json={'contracts':[
            {'symbol':'TESTCE','expiry':'2026-10-27','strike':25000,'type':'CE'},
            {'symbol':'TESTPE','expiry':'2026-10-27','strike':25000,'type':'PE'},
            {'symbol':'OTHERCE','expiry':'2026-10-27','strike':25100,'type':'CE'},
            {'symbol':'LATERCE','expiry':'2026-11-24','strike':25000,'type':'CE'}]}))
        page.route('**/api/option-chart?symbol=*',lambda r:r.fulfill(json={'token':111 if 'TESTCE' in r.request.url else 222, 'data':[{'time':1726707600,'open':100,'high':200,'low':90,'close':150 if 'TESTCE' in r.request.url else 160}]}))
        page.get_by_role('button',name='Explore options',exact=True).click()
        option_selector = page.get_by_label('Option strike',exact=True)
        expect(option_selector).to_have_value('TESTCE')
        expect(page.locator('.performance-chart .chart-readout')).to_contain_text('C 150')
        page.get_by_label('Find option strike',exact=True).fill('25100')
        option_selector.select_option('OTHERCE')
        page.get_by_role('button',name='Jump to ATM',exact=True).click()
        expect(option_selector).to_have_value('TESTCE')
        page.get_by_label('Option expiry',exact=True).select_option('2026-11-24')
        expect(option_selector).to_have_value('LATERCE')
        page.get_by_label('Option expiry',exact=True).select_option('2026-10-27')
        page.get_by_role('button',name='Put (PE)',exact=True).click()
        expect(option_selector).to_have_value('TESTPE')
        expect(page.locator('.performance-chart .chart-readout')).to_contain_text('C 160')
        page.get_by_role('button',name='Fit option chart data',exact=True).click()
        page.get_by_role('button',name='Show latest option candles',exact=True).click()
        page.get_by_role('button',name='Focus option chart',exact=True).click()
        expect(page.locator('.overview-chart-frame:fullscreen')).to_have_count(1)
        expect(page.locator('.overview-chart-frame:fullscreen .chart-readout')).to_contain_text('C 160')
        page.get_by_role('button',name='Focus option chart',exact=True).click()
        expect(page.locator('.overview-chart-frame:fullscreen')).to_have_count(0)
        page.screenshot(path=str(artifacts/'option-picker.png'),full_page=True)
        page.set_viewport_size({'width':390,'height':1000})
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'Option picker overflows mobile'
        page.screenshot(path=str(artifacts/'option-picker-mobile.png'),full_page=True)
        page.set_viewport_size({'width':1440,'height':1000})
        stream_requests = []
        def stream_fixture(route):
            assert route.request.headers.get('authorization', '').startswith('Bearer ')
            stream_requests.append(route.request.url)
            now = time.time()
            token = '111' if '111' in route.request.url else '222'
            price = 177 if token == '111' else 188
            snapshot = {'status':'connected','server_time':now,
                        'quotes':{'256265':{'price':25040,'time':now},token:{'price':price,'time':now}},
                        'candles':{'256265':[{'time':int(now//300)*300,'open':25035,'high':25040,'low':25034,'close':25040,'last_tick':now}],
                                   token:[{'time':int(now//300)*300,'open':170,'high':price,'low':169,'close':price,'last_tick':now}]}}
            route.fulfill(content_type='text/event-stream', body='data: '+json.dumps(snapshot)+'\n\n')
        page.route('**/api/market-stream?*',stream_fixture)
        page.get_by_role('button',name='Call (CE)',exact=True).click()
        expect(page.locator('.performance-chart .chart-readout')).to_contain_text('C 177')
        expect(page.locator('.main-chart .chart-readout')).to_contain_text('C 25,040')
        expect(page.get_by_text('Live · Kite stream',exact=False)).to_be_visible()
        page.get_by_role('button',name='Put (PE)',exact=True).click()
        expect(page.locator('.performance-chart .chart-readout')).to_contain_text('C 188')
        assert any('111' in url for url in stream_requests) and any('222' in url for url in stream_requests)
        page.get_by_role('button',name='Follow strategy',exact=True).click()
        expect(page.get_by_text('Follow your option premium',exact=True)).to_be_visible()
        expect(page.get_by_text('Kite connected — trading paused',exact=True)).to_be_visible()
        page.unroute('**/api/state')
        page.goto(base+'/charts')
        expect(page.get_by_text('20 candles').first).to_be_visible()
        assert page.locator('canvas').count() >= 2
        expect(page.locator('.chart-readout').first).to_contain_text('IST')
        chart = page.locator('ax-chart').first
        chart.scroll_into_view_if_needed()
        box = chart.bounding_box()
        page.mouse.move(box['x']+box['width']/2, box['y']+box['height']/2)
        before = page.evaluate('scrollY')
        page.mouse.wheel(0,150)
        page.wait_for_timeout(350)
        assert page.evaluate('scrollY') > before, 'Inactive chart must allow page scrolling'
        chart.get_by_role('button',name='Activate chart interactions').click()
        expect(chart).to_have_class('chart-selected')
        box = chart.bounding_box()
        page.mouse.move(box['x']+box['width']/2, box['y']+box['height']/2)
        before = page.evaluate('scrollY')
        page.mouse.wheel(0,150)
        page.wait_for_timeout(350)
        assert abs(page.evaluate('scrollY')-before) < 2, 'Selected chart must consume wheel zoom'
        page.mouse.dblclick(box['x']+box['width']/2, box['y']+box['height']/2)
        expect(chart.get_by_role('button',name='Activate chart interactions')).to_be_visible()
        chart.get_by_role('button',name='Activate chart interactions').click()
        page.keyboard.press('Escape')
        expect(chart.get_by_role('button',name='Activate chart interactions')).to_be_visible()
        chart.get_by_role('button',name='Activate chart interactions').click()
        page.locator('.pane-footer').first.click()
        expect(chart.get_by_role('button',name='Activate chart interactions')).to_be_visible()
        symbol=page.get_by_role('combobox',name='Chart 1 symbol',exact=True)
        symbol.fill('Reliance Industries')
        expect(page.get_by_role('option',name='RELIANCE Reliance Industries Limited')).to_be_visible()
        symbol.press('ArrowDown')
        symbol.press('Enter')
        expect(symbol).to_have_value('RELIANCE')
        expect(page.locator('.pane-footer').first).to_contain_text('RELIANCE')

        page.get_by_role('button',name='Focus chart 1',exact=True).click()
        expect(page.locator('.chart-panel:visible')).to_have_count(1)
        page.get_by_role('button',name='Fit data',exact=True).first.click()
        page.get_by_role('button',name='Latest',exact=True).first.click()
        page.get_by_role('button',name='Back to layout',exact=True).click()
        expect(page.locator('.chart-panel:visible')).to_have_count(2)
        with page.expect_request(lambda r: '/api/chart/history' in r.url and 'sessions=3' in r.url):
            page.get_by_label('History range',exact=True).select_option(label='Last 3 trading days · maximum detail')
        expect(page.get_by_label('Chart 1 timeframe',exact=True)).to_have_value('minute')
        with page.expect_request(lambda r: '/api/chart/history' in r.url and 'interval=week' in r.url and 'sessions=' not in r.url):
            page.get_by_label('Chart 1 timeframe',exact=True).select_option('week')
        expect(page.locator('.pane-footer').first).to_contain_text('Weekly · 90 days')
        page.get_by_label('Arrangement',exact=True).select_option('stack')
        page.get_by_role('button',name='Save workspace',exact=True).click()
        page.reload()
        expect(page.get_by_text('20 candles').first).to_be_visible()
        assert page.get_by_label('Arrangement',exact=True).input_value()=='stack'
        expect(page.get_by_label('Chart 1 timeframe',exact=True)).to_have_value('week')
        page.unroute('**/api/chart/history*')
        pending_history = []
        def delayed_history(route):
            if 'to=2024-09-01' in route.request.url:
                pending_history.append(route)
            else:
                route.fulfill(json={'ok':True,'data':candles,'next_to':'2024-09-01','complete':False})
        page.route('**/api/chart/history*', delayed_history)
        page.get_by_label('History range',exact=True).select_option(label='All available history')
        expect(page.locator('ax-chart[aria-busy="true"]')).to_have_count(2)
        expect(page.locator('.chart-loading')).to_have_count(2)
        expect(page.locator('.chart-host').first).to_be_hidden()
        page.wait_for_timeout(300)
        assert len(pending_history) == 2
        for route in pending_history:
            route.fulfill(json={'ok':True,'data':[], 'next_to':None,'complete':True})
        expect(page.locator('.chart-loading')).to_have_count(0)
        expect(page.locator('.chart-host').first).to_be_visible()
        page.unroute('**/api/chart/history*')
        def paged_history(route):
            older = 'to=2024-09-01' in route.request.url
            data = [{**c, 'time': c['time']-100000} for c in candles] if older else candles
            route.fulfill(json={'ok':True,'data':data,'next_to':None if older else '2024-09-01','complete':older})
        page.route('**/api/chart/history*', paged_history)
        page.get_by_role('button',name='Refresh all',exact=True).click()
        expect(page.get_by_text('All available history loaded',exact=True)).to_have_count(2)
        expect(page.locator('.pane-footer').first).to_contain_text('40 candles')
        page.get_by_role('button',name='Save workspace',exact=True).click()
        page.reload()
        expect(page.get_by_label('History range',exact=True)).to_have_value('all')
        expect(page.locator('.pane-footer').first).to_contain_text('40 candles')
        page.screenshot(path=str(artifacts/'populated-stacked-charts.png'),full_page=True)

        # Workspace subscriptions follow visible symbols; cached history accepts live updates.
        page.unroute('**/api/chart/history*')
        page.unroute('**/api/market-stream*')
        stream_tokens = []
        tick_time = int(time.time())
        minute_time = tick_time // 60 * 60
        def workspace_history(route):
            token = 738561 if 'symbol=RELIANCE' in route.request.url else 256265
            route.fulfill(json={'ok':True,'data':candles,'token':token,'cache_hit':True,
                                'history_as_of':tick_time-60,'next_to':None,'complete':True})
        def workspace_stream(route):
            from urllib.parse import parse_qs, urlparse
            tokens = parse_qs(urlparse(route.request.url).query)['tokens'][0].split(',')
            assert route.request.headers.get('authorization','').startswith('Bearer ')
            stream_tokens.append(set(tokens))
            snapshot = {'status':'connected','server_time':time.time(),
                        'quotes':{t:{'price':54321,'time':time.time()} for t in tokens},
                        'minute_candles':{t:[{'time':minute_time,'open':54320,'high':54322,
                                             'low':54319,'close':54321,'last_tick':tick_time}] for t in tokens}}
            route.fulfill(status=200,content_type='text/event-stream',body='data: '+json.dumps(snapshot)+'\n\n')
        page.route('**/api/chart/history*', workspace_history)
        page.route('**/api/market-stream*', workspace_stream)
        page.get_by_label('Chart 1 timeframe',exact=True).select_option('5minute')
        page.get_by_role('button',name='Refresh all',exact=True).click()
        expect(page.locator('.pane-footer').first).to_contain_text('History from cache · Live')
        expect(page.locator('.chart-panel .chart-readout').first).to_contain_text('54,321')
        expect(page.locator('.pane-footer').nth(1)).to_contain_text('Live')
        assert {'738561','256265'} in stream_tokens, stream_tokens
        page.get_by_role('button',name='Focus chart 1',exact=True).click()
        expect(page.locator('.chart-panel:visible')).to_have_count(1)
        page.wait_for_timeout(300)
        assert stream_tokens[-1] == {'738561'}, stream_tokens
        page.get_by_role('button',name='Back to layout',exact=True).click()
        expect(page.locator('.chart-panel:visible')).to_have_count(2)
        page.wait_for_timeout(300)
        assert stream_tokens[-1] == {'738561','256265'}, stream_tokens
        page.screenshot(path=str(artifacts/'live-cached-workspace.png'),full_page=True)

        page.route('**/api/backtest/run',lambda r:r.fulfill(json={'date':'2026-09-18','net_pnl':1250,'trade_taken':True,'position_type':'CALL','candles':candles,'used_real_options':False}))
        page.goto(base+'/backtests')
        page.get_by_role('button',name='Run backtest',exact=True).click()
        expect(page.get_by_text('Test results',exact=True)).to_be_visible()
        expect(page.get_by_text('COMPLETE',exact=True)).to_be_visible()
        page.get_by_role('button',name='Sign out',exact=True).click()
        page.wait_for_url('**/login')
        page.get_by_label('Email address').fill('user1@example.test')
        page.get_by_label('Password',exact=True).fill('TestPassword123')
        page.locator('form button.btn.primary').click()
        page.wait_for_url('**/overview')
        expect(page.get_by_text('Paper and historical backtests are available. Live trading requires an administrator account.')).to_be_visible()
        page.get_by_label('Execution mode',exact=True).select_option('BACKTEST')
        page.wait_for_url('**/backtests')
        expect(page.get_by_role('button',name='Run backtest',exact=True)).to_be_enabled()
        page.goto(base+'/overview')
        expect(page.get_by_label('Execution mode',exact=True)).to_have_value('BACKTEST')
        page.get_by_label('Execution mode',exact=True).select_option('PAPER')
        expect(page.get_by_role('button',name='Enable paper trading')).to_be_visible()
        assert not errors, errors
        browser.close()
        print(f'PASS: 36 responsive route checks, strategy CRUD/cancel, profile, outage recovery, sign-out, role guards and populated historical chart fixtures. Screenshots: {artifacts}')
finally:
    server.shutdown()
