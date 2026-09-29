"""Import a representative Kite Console workbook through the authenticated API."""
import io
import unittest

import pandas as pd
import test_dashboard as fixtures


class TradeExcelImportTests(unittest.TestCase):
    def test_xlsx_import_creates_import_trade(self):
        fixtures.DashboardSmokeTests.setUpClass()
        workbook = io.BytesIO()
        pd.DataFrame([
            ['Symbol', 'Buy Date', 'Buy Qty', 'Buy Avg', 'Sell Date', 'Sell Avg', 'P&L', 'Charges'],
            ['NIFTY26SEP23000CE', '29/09/2026', 65, 100, '29/09/2026', 120, 1300, 60],
        ]).to_excel(workbook, index=False, header=False)
        workbook.seek(0)
        response = fixtures.DashboardSmokeTests.client.post(
            '/api/analytics/import-csv',
            data={'file': (workbook, 'kite-trades.xlsx')},
            content_type='multipart/form-data',
            headers=fixtures.DashboardSmokeTests.headers[0],
        )
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json['inserted'], 1)
        from db.database import SessionLocal
        from db.models import Trade
        with SessionLocal() as db:
            trade = db.query(Trade).filter_by(user_id=1, trade_mode='IMPORT', symbol='NIFTY26SEP23000CE').one()
            self.assertEqual(trade.net_pnl, 1240)
            db.delete(trade)
            db.commit()
