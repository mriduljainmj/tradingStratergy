"""Shared API boundary: typed input, auth lifecycle and stable JSON errors."""
import datetime as dt
import math
from flask import jsonify, request
from flask.json.provider import DefaultJSONProvider
from flask_jwt_extended import verify_jwt_in_request, get_jwt_identity
from werkzeug.exceptions import BadRequest, HTTPException
from db.database import SessionLocal
from db.models import User


def finite_json(value):
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {k: finite_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [finite_json(v) for v in value]
    return value


class APIJSONProvider(DefaultJSONProvider):
    def dumps(self, obj, **kwargs):
        kwargs['allow_nan'] = False
        return super().dumps(finite_json(obj), **kwargs)


def bad(message):
    raise BadRequest(message)


def number(value, label, minimum=0, maximum=1e9, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        bad(f'{label} must be a finite number.')
    if value < minimum or value > maximum or (integer and int(value) != value):
        bad(f'{label} must be {"an integer " if integer else ""}between {minimum} and {maximum}.')
    return value


def validate_settings(data):
    from config.config_utils import ALL_FIELDS, _TIME_FIELDS
    from config.settings import TradingConfig
    from config.config_utils import config_to_dict
    for key, val in data.items():
        if key not in ALL_FIELDS and key not in ('mode', 'trade_direction'):
            bad(f'Unknown setting: {key}')
        if key in _TIME_FIELDS:
            try:
                dt.time.fromisoformat(val)
            except (ValueError, TypeError):
                bad(f'{key} must use HH:MM format.')
        elif key in ALL_FIELDS:
            minimum = 1 if key in ('target_pts', 'lot_size', 'qty_multiplier', 'strike_spacing') else 0
            maximum = 1 if key.endswith('_pct') or key in ('fib_trail', 'assumed_iv', 'risk_free_rate') else 1e7
            number(val, key, minimum, maximum, key in ('target_pts', 'lot_size', 'qty_multiplier', 'strike_spacing'))
    if data.get('trade_direction', 'BOTH') not in ('CALL', 'PUT', 'BOTH'):
        bad('trade_direction must be CALL, PUT or BOTH.')
    # Validate ordering against defaults; callers merge saved settings first.
    merged = {**config_to_dict(TradingConfig()), **data}
    if not (dt.time.fromisoformat(merged['or_end_time']) < dt.time.fromisoformat(merged['entry_end_time']) <= dt.time.fromisoformat(merged['eod_exit_time'])):
        bad('Trading windows must satisfy opening range < last entry <= forced exit.')


def validate_strategy(data, existing=None):
    kind = data.get('instrument_type', getattr(existing, 'instrument_type', None) or 'OPTIONS')
    if kind not in ('OPTIONS', 'EQUITY'):
        bad('instrument_type must be OPTIONS or EQUITY.')
    engine = data.get('engine_type', getattr(existing, 'engine_type', None) or ('ORB' if kind == 'OPTIONS' else 'EQUITY_ORB'))
    if engine not in (('ORB',) if kind == 'OPTIONS' else ('EQUITY_ORB', 'EMA_CROSS')):
        bad('Unsupported engine for this instrument type.')
    rules = data.get('rules', existing.get_rules() if existing else {})
    if not isinstance(rules, dict):
        bad('Rules must be an object.')
    if kind == 'OPTIONS' and 'entry' in rules:
        if not isinstance(rules['entry'], dict):
            bad('entry must be an object.')
        conditions = rules['entry'].get('conditions')
        if not isinstance(conditions, list) or not conditions or not all(isinstance(c, dict) for c in conditions):
            bad('Entry conditions must be a non-empty list of objects.')
    for key in ('qty', 'lots', 'lot_size', 'ema_fast', 'ema_slow', 'target_pts', 'strike_spacing'):
        if key in rules:
            number(rules[key], key, 1, 100000, True)
    for key in ('sl_pct', 'tgt_pct'):
        if key in rules:
            number(rules[key], key, .01, 100)
    for key in ('fib_trail', 'slippage_pct'):
        if key in rules:
            number(rules[key], key, 0, 1)
    if 'max_daily_loss' in rules:
        number(rules['max_daily_loss'], 'max_daily_loss')
    if rules.get('ema_fast', 9) >= rules.get('ema_slow', 21):
        bad('Fast EMA must be less than slow EMA.')
    if rules.get('direction', 'LONG' if kind == 'EQUITY' else 'BOTH') not in (('LONG', 'SHORT') if kind == 'EQUITY' else ('CALL', 'PUT', 'BOTH')):
        bad('Invalid trade direction.')
    for key in ('or_end_time', 'entry_end_time', 'eod_exit_time', 'eod_exit'):
        if key in rules:
            try:
                dt.time.fromisoformat(rules[key])
            except (TypeError, ValueError):
                bad(f'Invalid {key}.')
    if kind == 'OPTIONS':
        from config.config_utils import ALL_FIELDS
        overrides = {k:v for k,v in rules.items() if k in ALL_FIELDS}
        validate_settings(overrides)


def install_api_support(app):
    app.json = APIJSONProvider(app)
    app.config['MAX_CONTENT_LENGTH'] = 5 * 1024 * 1024

    @app.before_request
    def boundary():
        if not request.path.startswith('/api/'):
            return
        public = request.path in ('/api/auth/login', '/api/auth/register')
        if not public:
            verify_jwt_in_request()
            try:
                uid = int(get_jwt_identity())
            except (ValueError, TypeError):
                return jsonify(ok=False, error='Invalid session.'), 401
            with SessionLocal() as db:
                if not db.get(User, uid):
                    return jsonify(ok=False, error='Account no longer exists.'), 401
        for key in ('from', 'to', 'from_date', 'to_date', 'date'):
            val = request.args.get(key)
            if val:
                try:
                    dt.date.fromisoformat(val)
                except ValueError:
                    bad(f'{key} must use YYYY-MM-DD format.')
        if request.args.get('from') and request.args.get('to') and request.args['from'] > request.args['to']:
            bad('From date must not follow To date.')
        for key in ('page', 'per_page', 'strategy_id'):
            if key in request.args:
                value = request.args[key]
                if not value.isdigit() or int(value) < 1 or (key == 'per_page' and int(value) > 100):
                    bad(f'Invalid {key}.')
        if request.method in ('POST', 'PUT', 'PATCH') and not request.mimetype == 'multipart/form-data':
            data = request.get_json() if request.data else {}
            if not isinstance(data, dict):
                bad('Request body must be a JSON object.')
            string_fields = {'email','username','password','name','description','symbol','instrument_type','engine_type','mode','direction','action','display_name','bio','broker_id','old_password','new_password','api_key','api_secret','access_token','request_token','list','sector','trade_direction','from_date','to_date','date','metric'}
            for key in string_fields & data.keys():
                if not isinstance(data[key], str):
                    bad(f'{key} must be a string.')
                limit = 500 if key in ('bio','description') else 255
                if key not in ('access_token', 'request_token') and len(data[key]) > limit:
                    bad(f'{key} is too long.')
            for key in ('enabled','force','trade_confirm_modal'):
                if key in data and not isinstance(data[key], bool):
                    bad(f'{key} must be a boolean.')
            for key in ('password','new_password','old_password'):
                if key in data and len(data[key].encode()) > 72:
                    bad('Password must not exceed 72 UTF-8 bytes.')
            if 'photo_base64' in data and data['photo_base64'] and (not isinstance(data['photo_base64'], str) or not data['photo_base64'].startswith(('data:image/png;base64,','data:image/jpeg;base64,','data:image/webp;base64,'))):
                bad('Use a PNG, JPEG or WebP profile photo.')
            if 'strategy_id' in data:
                number(data['strategy_id'], 'strategy_id', 1, 1e9, True)
            if request.path == '/api/strategies' and request.method == 'POST':
                validate_strategy(data)
            if request.path in ('/api/backtest/run','/api/backtest/optimize'):
                keys = ('date',) if data.get('mode', 'single') == 'single' and request.path.endswith('/run') else ('from_date','to_date')
                for key in keys:
                    try:
                        dt.date.fromisoformat(data.get(key, ''))
                    except (ValueError, TypeError):
                        bad(f'{key} must use YYYY-MM-DD format.')
                if data.get('from_date') and data.get('to_date'):
                    start = dt.date.fromisoformat(data['from_date'])
                    end = dt.date.fromisoformat(data['to_date'])
                    if start > end or (end-start).days > 365:
                        bad('Select an ordered date range of at most 365 days.')
                if 'target_pts' in data:
                    number(data['target_pts'], 'target_pts', 1, 100000, True)
                if 'direction' in data and data['direction'] not in ('CALL','PUT','BOTH'):
                    bad('Invalid trade direction.')
                if 'or_end_time' in data:
                    try:
                        dt.time.fromisoformat(data['or_end_time'])
                    except (ValueError, TypeError):
                        bad('Invalid opening range time.')
                if request.path.endswith('/optimize'):
                    for key in ('targets','or_times','directions'):
                        if key in data and (not isinstance(data[key], list) or not data[key] or len(data[key]) > 20):
                            bad(f'{key} must contain between 1 and 20 values.')
                    for target in data.get('targets', []):
                        number(target, 'target', 1, 100000, True)
                    for value in data.get('or_times', []):
                        try:
                            dt.time.fromisoformat(value)
                        except (TypeError, ValueError):
                            bad('Invalid opening time.')
                    if any(d not in ('CALL','PUT','BOTH') for d in data.get('directions', [])):
                        bad('Invalid optimization direction.')
                    if len(data.get('targets', [0]*7))*len(data.get('or_times', [0]*6))*len(data.get('directions', [0]*3)) > 200:
                        bad('Limit optimization to 200 parameter combinations.')
            if request.path == '/api/trades':
                if 'date' in data:
                    try:
                        dt.date.fromisoformat(data['date'])
                    except ValueError:
                        bad('Invalid trade date.')
                for key in ('entry_prem','exit_prem','charges','strike','quantity','gross_pnl','net_pnl','or_high','or_low'):
                    if key in data and data[key] is not None:
                        number(data[key], key, -1e12 if key in ('gross_pnl','net_pnl') else 0, 1e12, key in ('strike','quantity'))
                if data.get('trade_mode','PAPER') not in ('PAPER','LIVE','IMPORT'):
                    bad('Invalid trade mode.')
            if request.path in ('/api/trades-enabled' ,'/api/background-trading') and 'enabled' not in data:
                bad('enabled is required.')

        if not public and request.method in ('POST','PUT','DELETE'):
            from core.engine_pool import engine_pool
            from execution.order_safety import has_exposure, unresolved_orders
            ue = engine_pool.get(uid)
            sensitive = request.path in ('/api/auth/account','/api/auth/kite-credentials','/api/mode','/api/settings','/api/active-strategy')
            sensitive = sensitive or (request.path == '/api/auth/kite-token' and request.method == 'DELETE')
            if sensitive and (has_exposure(ue) or unresolved_orders(uid)):
                return jsonify(ok=False, error='Close positions and reconcile outstanding orders before making this change.'), 409
            if request.path in ('/api/backtest/run','/api/backtest/optimize'):
                ue = engine_pool.get_or_create(uid)
                if ue.state.kite_auth_error or not ue.broker.kite.access_token:
                    return jsonify(ok=False, error='Connect Kite before requesting historical data.'), 409

    @app.errorhandler(HTTPException)
    def http_error(exc):
        if request.path.startswith('/api/'):
            return jsonify(ok=False, error=exc.description), exc.code
        return exc

    @app.errorhandler(Exception)
    def unexpected(exc):
        app.logger.exception('Unhandled request failure')
        return jsonify(ok=False, error='The request failed. Please retry or check server logs.'), 500

    @app.after_request
    def response_headers(response):
        if request.path.startswith('/api/') and response.status_code >= 500 and response.is_json:
            response.set_data(app.json.dumps({'ok': False, 'error': 'The server could not complete the request. Check server logs or retry.'}))
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'same-origin'
        if request.path.startswith('/api/'):
            response.headers['Cache-Control'] = 'no-store'
        return response
