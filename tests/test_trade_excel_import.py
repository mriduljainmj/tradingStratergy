"""Import a representative Kite Console workbook through the authenticated API."""
import io
import unittest
from unittest.mock import patch
import datetime

import pandas as pd
import test_dashboard as fixtures


class TradeExcelImportTests(unittest.TestCase):
    def test_tax_report_uses_symbol_summaries_and_allocates_sheet_charges(self):
        from dashboard.analytics_routes import _parse_zerodha_taxpnl

        class Sheet:
            title = 'Equity and Non Equity'

            def iter_rows(self, values_only=False):
                rows = [
                    (None, 'Taxpnl Statement for Equity from 2026-04-01 to 2026-09-29'),
                    (None, 'Charges'),
                    (None, 'Brokerage - Z', 20),
                    (None, 'IPFT', 10),
                    (None, 'Other Charges'),
                    (None, 'Equity Intraday'),
                    (None, 'Symbol', 'Quantity', 'Buy Value', 'Sell Value', 'Realized P&L'),
                    (None, 'TEST', 10, 1000, 1200, 200),
                    (None, 'Equity Short Term'),
                    (None, 'Symbol', 'Quantity', 'Buy Value', 'Sell Value', 'Realized P&L'),
                    (None, 'SECOND', 2, 400, 500, 100),
                ]
                for row in rows:
                    yield row + (None,) * (8 - len(row))

        with patch('openpyxl.load_workbook', return_value=[Sheet()]):
            rows = _parse_zerodha_taxpnl(b'fake')
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows.iloc[0]['buy_price'], 100)
        self.assertEqual(rows.iloc[0]['sell_price'], 120)
        self.assertEqual(rows.charges.sum(), 30)
        self.assertEqual(rows.net_pnl.sum(), 270)
        self.assertEqual(rows.iloc[0]['trade_date'], datetime.date(2026, 9, 29))
        self.assertEqual(rows.iloc[1]['symbol'], 'SECOND')
        self.assertEqual(rows.iloc[0]['strategy_name'], 'Kite Tax P&L · Equity Intraday')

    def test_fo_summary_classifies_options(self):
        from dashboard.analytics_routes import _parse_zerodha_taxpnl

        class Sheet:
            title = 'F&O'

            def iter_rows(self, values_only=False):
                rows = [
                    (None, 'Taxpnl Statement for F&O from 2026-04-01 to 2026-09-29'),
                    (None, 'Options'),
                    (None, 'Symbol', 'Quantity', 'Buy Value', 'Sell Value', 'Realized P&L'),
                    (None, 'NIFTY26SEP23000CE', 65, 6500, 7800, 1300),
                ]
                for row in rows:
                    yield row + (None,) * (8 - len(row))

        with patch('openpyxl.load_workbook', return_value=[Sheet()]):
            rows = _parse_zerodha_taxpnl(b'fake')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows.iloc[0]['position_type'], 'CALL')
        self.assertEqual(rows.iloc[0]['net_pnl'], 1300)

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
