import unittest
import datetime
from unittest.mock import patch
from werkzeug.exceptions import BadRequest
import test_dashboard  # Configure the isolated test database before importing routes.
from config.settings import TradingConfig
from dashboard.routes import _request_backtest_config


class BacktestParametersTests(unittest.TestCase):
    def test_all_overrides_reach_request_config(self):
        cfg = TradingConfig()
        values = dict(entry_end_time='12:00', eod_exit_time='14:30', fib_trail=.6,
                      lot_size=65, qty_multiplier=2, strike_spacing=50, assumed_iv=.2,
                      risk_free_rate=.05, brokerage_per_order=15, stt_pct=.001,
                      exchange_charges_pct=.0004, gst_pct=.18, sebi_charges_pct=.000002,
                      stamp_duty_pct=.00004, slippage_pct=.002)
        with patch('dashboard.routes._backtest_config', return_value=cfg):
            result = _request_backtest_config(dict(parameters=values, target_pts=80, or_end_time='09:35'))
        for key, value in values.items():
            expected = datetime.time.fromisoformat(value) if key.endswith('_time') else value
            self.assertEqual(getattr(result, key), expected)
        self.assertEqual(result.qty, 130)
        self.assertEqual(result.target_pts, 80)

    def test_invalid_parameters_and_optimization_windows_rejected(self):
        for data in [dict(parameters={'fib_trail': '0.6'}), dict(parameters={'lot_size': 1.5}),
                     dict(parameters={'unknown': 1}), dict(parameters=[]),
                     dict(parameters={'entry_end_time': '09:20'}),
                     dict(parameters={'entry_end_time': '10:00'}, or_times=['10:15'])]:
            with self.subTest(data=data), patch('dashboard.routes._backtest_config', return_value=TradingConfig()):
                with self.assertRaises(BadRequest):
                    _request_backtest_config(data)
