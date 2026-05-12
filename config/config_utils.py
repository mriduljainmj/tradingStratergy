"""
config_utils.py — Shared helpers for serialising / deserialising TradingConfig.

Imported by both dashboard.routes and core.engine_pool so neither needs to
know about the other's internals, and there are no circular imports.
"""

import datetime
import json
import logging

from config.settings import TradingConfig

logger = logging.getLogger(__name__)

_STRATEGY_FIELDS = ["target_pts", "fib_trail", "entry_end_time", "eod_exit_time", "strike_spacing"]
_POSITION_FIELDS = ["lot_size", "qty_multiplier"]
_OPTIONS_FIELDS  = ["risk_free_rate", "assumed_iv"]
_BROKER_FIELDS   = [
    "brokerage_per_order", "stt_pct", "exchange_charges_pct",
    "gst_pct", "sebi_charges_pct", "stamp_duty_pct",
]
_TIME_FIELDS = {"entry_end_time", "eod_exit_time"}

ALL_FIELDS = _STRATEGY_FIELDS + _POSITION_FIELDS + _OPTIONS_FIELDS + _BROKER_FIELDS


def config_to_dict(cfg: TradingConfig) -> dict:
    """Serialise the editable fields of a TradingConfig to a plain dict."""
    result = {}
    for field in ALL_FIELDS:
        val = getattr(cfg, field, None)
        if isinstance(val, datetime.time):
            val = val.strftime("%H:%M")
        result[field] = val
    return result


def apply_config_dict(cfg: TradingConfig, data: dict):
    """Apply a dict of field overrides to an existing TradingConfig in-place."""
    for field in ALL_FIELDS:
        if field not in data:
            continue
        val = data[field]
        if field in _TIME_FIELDS:
            try:
                # Accept both "HH:MM" and "HH:MM:SS" (some browsers append seconds)
                parts = str(val).split(":")
                h, m = int(parts[0]), int(parts[1])
                setattr(cfg, field, datetime.time(h, m))
                logger.debug(f"Settings: {field} → {h:02d}:{m:02d}")
            except Exception as e:
                logger.warning(f"Settings: failed to apply {field}={val!r}: {e}")
        else:
            current = getattr(cfg, field, None)
            try:
                setattr(cfg, field, type(current)(val))
                logger.debug(f"Settings: {field} → {getattr(cfg, field)}")
            except Exception as e:
                logger.warning(f"Settings: failed to apply {field}={val!r}: {e}")


def apply_settings_json(cfg: TradingConfig, settings_json: str):
    """Load settings from a JSON string and apply to cfg in-place."""
    if not settings_json:
        return
    try:
        data = json.loads(settings_json)
        apply_config_dict(cfg, data)
    except Exception as e:
        logger.warning(f"Settings: failed to apply settings JSON: {e}")
