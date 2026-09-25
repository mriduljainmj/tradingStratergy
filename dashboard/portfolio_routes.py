"""Read-only portfolio snapshots from the signed-in user's own Kite session."""
import datetime
import logging
from flask import Blueprint, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity
from core.engine_pool import engine_pool
from execution.broker import is_kite_auth_error

portfolio_bp = Blueprint('portfolio', __name__)
logger = logging.getLogger(__name__)


@portfolio_bp.get('/api/portfolio')
@jwt_required()
def portfolio():
    ue = engine_pool.get_or_create(int(get_jwt_identity()))
    if ue.state.kite_auth_error or not ue.broker.kite.access_token:
        return jsonify(ok=False, error='Connect Kite in Profile to view your portfolio.'), 409
    try:
        holdings = ue.broker.kite.holdings()
        positions = ue.broker.kite.positions()
        if (not isinstance(holdings, list) or not isinstance(positions, dict)
                or not isinstance(positions.get('net'), list)):
            raise ValueError('Invalid portfolio response')
        common = ('tradingsymbol', 'exchange', 'product', 'quantity', 'average_price', 'last_price', 'pnl')
        def clean(rows, fields):
            if any(not isinstance(row, dict) or not row.get('tradingsymbol') for row in rows):
                raise ValueError('Invalid portfolio row')
            cleaned = []
            for row in rows:
                item = {key: row.get(key) for key in fields}
                if 'mtf' in fields:
                    mtf = row.get('mtf')
                    item['mtf'] = ({key: mtf.get(key) for key in ('quantity', 'average_price')}
                                   if isinstance(mtf, dict) else None)
                cleaned.append(item)
            return cleaned
        return jsonify(ok=True,
                       holdings=clean(holdings, common + ('t1_quantity', 'used_quantity', 'collateral_quantity', 'day_change_percentage', 'discrepancy', 'mtf')),
                       positions=clean(positions['net'], common + ('unrealised', 'realised',)),
                       fetched_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
    except Exception as exc:
        if is_kite_auth_error(exc):
            ue.state.kite_auth_error = True
            return jsonify(ok=False, error='Your Kite session expired. Reconnect in Profile to view your portfolio.'), 409
        logger.warning('Portfolio fetch failed for user %s (%s)', ue.user_id, type(exc).__name__)
        return jsonify(ok=False, error='Kite portfolio is currently unavailable. Retry to fetch a fresh snapshot.'), 502
