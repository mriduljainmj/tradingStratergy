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

from unittest.mock import patch
quote_patch = patch('dashboard.screener_routes._get_broker', return_value=None)
quote_patch.start()
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
        page.get_by_label('Email address').fill('user0@example.test')
        page.get_by_label('Password', exact=True).fill('TestPassword123')
        page.locator('form button.btn.primary').click()
        page.wait_for_url('**/overview')

        import datetime
        candles=[{'time':str(datetime.date(2026,1,1)+datetime.timedelta(days=i)), 'open':100+i,'high':105+i,'low':98+i,'close':103+i,'volume':1000} for i in range(60)]
        page.route('**/api/chart/history*',lambda r:r.fulfill(json={'ok':True,'data':candles,'complete':True}))
        page.goto(base+'/markets')
        page.get_by_role('button',name='+ New watchlist',exact=True).click()
        page.get_by_label('New watchlist name').fill('Swing trades')
        page.get_by_role('button',name='Create',exact=True).click()
        expect(page.get_by_label('Selected watchlist')).to_have_value('Swing trades')
        token=page.evaluate("sessionStorage.getItem('axiom_token')")
        headers={'Authorization':'Bearer '+token}
        assert page.request.post(base+'/api/screener/watchlist',headers=headers,data={'symbol':'RELIANCE','list':'Swing trades'}).ok
        page.goto(base+'/charts')
        page.get_by_label('Selected watchlist').select_option('Swing trades')
        page.get_by_role('button',name='Open RELIANCE in chart 1').click()
        chart=page.locator('ax-chart').first
        save=chart.get_by_role('button',name='Save drawings & indicators',exact=True)
        expect(save).to_be_enabled()
        chart.get_by_role('button',name='Indicators',exact=True).click()
        chart.get_by_label('Indicator period',exact=True).fill('3')
        chart.get_by_role('button',name='Add',exact=True).click()
        chart.get_by_role('button',name='Close indicator settings').click()
        chart.get_by_role('button',name='Line tools',exact=True).click()
        chart.get_by_role('menuitem',name='Horizontal level',exact=True).click()
        plot=chart.locator('.chart-host').bounding_box()
        page.mouse.click(plot['x']+plot['width']*.45,plot['y']+plot['height']*.6)
        expect(chart.locator('.drawing-layer line')).to_have_count(1)
        save.click()
        expect(chart.get_by_text('Saved to your account.',exact=True)).to_be_visible()
        saved=page.request.get(base+'/api/workspace/annotations?context=RELIANCE:day',headers=headers).json()
        assert len(saved['layout']['drawings'])==1 and len(saved['layout']['studies'])==1,saved
        page.reload()
        page.get_by_label('Selected watchlist').select_option('Swing trades')
        page.get_by_role('button',name='Open RELIANCE in chart 1').click()
        expect(chart.get_by_role('button',name='Remove SMA indicator')).to_be_visible()
        expect(chart.locator('.drawing-layer line')).to_have_count(1)
        page.get_by_label('Chart 1 timeframe',exact=True).select_option('week')
        expect(chart.get_by_role('button',name='Remove SMA indicator')).to_have_count(0)
        expect(chart.locator('.drawing-layer line')).to_have_count(0)
        page.get_by_label('Chart 1 timeframe',exact=True).select_option('day')
        expect(chart.get_by_role('button',name='Remove SMA indicator')).to_be_visible()
        expect(chart.locator('.drawing-layer line')).to_have_count(1)
        page.screenshot(path=str(artifacts/'saved-watchlists-desktop.png'),full_page=True)
        for width in [390,320]:
            page.set_viewport_size({'width':width,'height':900})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'),width
        page.screenshot(path=str(artifacts/'saved-watchlists-mobile.png'),full_page=True)
        assert not errors,errors
        print('PASS create/select named lists, drawings and indicators survive reload, timeframe isolation, mobile bounds:',artifacts)
        browser.close()
finally:
    server.shutdown()
    quote_patch.stop()
