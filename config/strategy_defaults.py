"""Default strategy seed values come from the same configuration as the engine."""
from config.settings import TradingConfig
from config.config_utils import config_to_dict


def default_options_rules():
    config = TradingConfig()
    values = config_to_dict(config)
    keys = ('target_pts', 'fib_trail', 'or_end_time', 'entry_end_time',
            'eod_exit_time', 'strike_spacing', 'lot_size', 'max_daily_loss')
    return {**{key: values[key] for key in keys}, 'lots': config.qty_multiplier, 'direction': 'BOTH'}
