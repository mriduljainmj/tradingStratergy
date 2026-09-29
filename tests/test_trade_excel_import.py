"""Import a representative Kite Console workbook through the authenticated API."""
import io
import unittest
from unittest.mock import patch
import datetime

import pandas as pd
import test_dashboard as fixtures


class TradeExcelImportTests(unittest.TestCase):
    def test_multisection_tax_report_uses_total_values_and_realized_charges(self):
        from dashboard.analytics_routes import _parse_zerodha_taxpnl

        class Sheet:
            title = 'Tradewise Exits from 2026-04-01'

            def iter_rows(self, values_only=False):
                yield (None, 'Equity - Intraday') + (None,) * 13
                yield (None, 'Symbol', 'ISIN', 'Entry Date', 'Exit Date', 'Quantity',
                       'Buy Value', 'Sell Value', 'Profit', 'Turnover', 'Brokerage',
                       'Exchange Transaction Charges', 'IPFT', 'SEBI Charges',
                       'STT')
                yield (None, 'TEST', 'TESTISIN', '2026-09-28', '2026-09-29', 10,
                       1000, 1200, 200, 2200, 20, 2, 1, 1, 4)
                yield (None, 'Equity - Short Term') + (None,) * 13
                yield (None, 'Symbol', 'ISIN', 'Entry Date', 'Exit Date', 'Quantity',
                       'Buy Value', 'Sell Value', 'Profit', 'Turnover', 'Brokerage',
                       'Exchange Transaction Charges', 'IPFT', 'SEBI Charges',
                       'STT')
                yield (None, 'SECOND', 'TESTISIN', '2026-09-28', '2026-09-29', 2,
                       400, 500, 100, 900, 0, 0, 0, 0, 0)

        with patch('openpyxl.load_workbook', return_value=[Sheet()]):
            rows = _parse_zerodha_taxpnl(b'fake')
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows.iloc[0]['buy_price'], 100)
        self.assertEqual(rows.iloc[0]['sell_price'], 120)
        self.assertEqual(rows.iloc[0]['charges'], 28)
        self.assertEqual(rows.iloc[0]['net_pnl'], 172)
        self.assertEqual(rows.iloc[0]['trade_date'], datetime.date(2026, 9, 28))
        self.assertEqual(rows.iloc[1]['symbol'], 'SECOND')

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
