"""Manual cash-equity orders. All broker mutations require a reviewed, single-use intent."""
import datetime as dt
import hashlib
import json
import math
import uuid
from decimal import Decimal
from functools import wraps

from flask import Blueprint, current_app, jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required
from itsdangerous import URLSafeTimedSerializer, BadSignature
from kiteconnect.exceptions import KiteException, InputException, OrderException, TokenException, PermissionException
from sqlalchemy.exc import IntegrityError
from werkzeug.exceptions import HTTPException, Conflict

from core.engine_pool import engine_pool
from dashboard.api_support import bad, number
from dashboard.authz import admin_required
from db.database import SessionLocal
from db.models import InstrumentCatalog, ManualEquityOrder
from execution.broker import is_kite_auth_error
from execution.order_safety import serialized_order, has_exposure, unresolved_orders

bp = Blueprint('equity_orders', __name__)
OPEN = {'OPEN', 'TRIGGER PENDING', 'AMO REQ RECEIVED'}
TERMINAL = {'COMPLETE', 'CANCELLED', 'REJECTED'}
FIELDS = ('order_id', 'tradingsymbol', 'exchange', 'product', 'transaction_type',
          'order_type', 'quantity', 'filled_quantity', 'pending_quantity', 'average_price',
          'price', 'trigger_price', 'validity', 'disclosed_quantity', 'variety', 'status',
          'status_message', 'order_timestamp', 'tag')


def connection():
    ue = engine_pool.get_or_create(int(get_jwt_identity()))
    if ue.state.kite_auth_error or not ue.broker.kite.access_token:
        raise Conflict('Connect Kite in Profile before accessing equity orders.')
    return ue, ue.broker.kite


def live(ue):
    if ue.state.app_mode != 'LIVE':
        raise Conflict('Select Live mode in Overview before submitting real equity orders.')
    if ue.is_running or has_exposure(ue) or any(e.running for e in ue.equity_engines.values()):
        raise Conflict('Stop automated strategies and resolve their positions before manual equity trading.')


def handled(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except HTTPException:
            raise
        except Exception as exc:
            if is_kite_auth_error(exc):
                return jsonify(ok=False, error='Kite session expired. Reconnect in Profile.'), 409
            if isinstance(exc, (InputException, OrderException, PermissionException)):
                return jsonify(ok=False, error=str(exc)[:500]), 400
            current_app.logger.warning('Equity API failure: %s', type(exc).__name__)
            return jsonify(ok=False, error='Could not verify data with Kite. Refresh and retry.'), 502
    return wrapped


def instruments(kite, exchange):
    if exchange not in ('NSE', 'BSE'):
        bad('Choose NSE or BSE.')
    today = dt.datetime.now(dt.timezone(dt.timedelta(hours=5, minutes=30))).date()
    # Separate catalogue key: existing chart catalogue does not include tick size / lot size.
    key = exchange + '_CASH'
    with SessionLocal() as db:
        row = db.get(InstrumentCatalog, key)
        if row and row.fetched_on == today:
            return json.loads(row.payload)
        source = kite.instruments(exchange)
        records = [{k: r.get(k) for k in ('tradingsymbol', 'name', 'exchange', 'tick_size', 'lot_size')}
                   for r in source if r.get('instrument_type') == 'EQ' and r.get('exchange') == exchange
                   and r.get('segment') == exchange]
        if not records:
            raise ValueError('Empty equity catalogue')
        if row:
            row.payload, row.fetched_on = json.dumps(records), today
        else:
            db.add(InstrumentCatalog(exchange=key, payload=json.dumps(records), fetched_on=today))
        db.commit()
        return records


def normalize(kite, data):
    if not isinstance(data, dict):
        bad('Order must be an object.')
    enums = {'exchange': ('NSE', 'BSE'), 'transaction_type': ('BUY', 'SELL'),
             'product': ('CNC', 'MIS'), 'order_type': ('MARKET', 'LIMIT', 'SL', 'SL-M'),
             'variety': ('regular', 'amo'), 'validity': ('DAY', 'IOC')}
    p = {}
    for key, choices in enums.items():
        if data.get(key) not in choices:
            bad(f'{key} must be one of: {", ".join(choices)}.')
        p[key] = data[key]
    symbol = data.get('tradingsymbol')
    instrument = next((r for r in instruments(kite, p['exchange']) if r['tradingsymbol'] == symbol), None)
    if not instrument:
        bad('Select a valid cash-equity instrument from search.')
    p['tradingsymbol'] = symbol
    p['quantity'] = int(number(data.get('quantity'), 'Quantity', 1, 10000000, True))
    lot = instrument.get('lot_size') or 1
    if p['quantity'] % lot:
        bad(f'Quantity must be a multiple of {lot}.')
    tick = Decimal(str(instrument.get('tick_size') or 0))
    if tick <= 0:
        bad('Instrument tick size unavailable; retry after refreshing instruments.')
    for field, needed in [('price', p['order_type'] in ('LIMIT', 'SL')),
                          ('trigger_price', p['order_type'] in ('SL', 'SL-M'))]:
        val = number(data.get(field, 0), field, 0, 1e8)
        if needed and (val <= 0 or Decimal(str(val)) % tick != 0):
            bad(f'{field} must be positive and a multiple of {tick}.')
        p[field] = val if needed else 0
    if p['order_type'] == 'SL':
        if ((p['transaction_type'] == 'BUY' and p['price'] < p['trigger_price']) or
                (p['transaction_type'] == 'SELL' and p['price'] > p['trigger_price'])):
            bad('Stop-limit buy price must be at least the trigger; sell price must be at most the trigger.')
    if p['validity'] == 'IOC' and (p['variety'] == 'amo' or p['order_type'] in ('SL', 'SL-M')):
        bad('IOC is available for regular Market and Limit orders only.')
    p['disclosed_quantity'] = int(number(data.get('disclosed_quantity', 0), 'Disclosed quantity', 0, p['quantity'], True))
    if p['disclosed_quantity'] and p['disclosed_quantity'] < math.ceil(p['quantity'] * .1):
        bad('Disclosed quantity must be at least 10% of the order quantity.')
    if p['order_type'] in ('MARKET', 'SL-M'):
        protection = data.get('market_protection', -1)
        if protection != -1:
            number(protection, 'Market protection %', .01, 100)
        p['market_protection'] = protection
    return p


def orderbook(kite):
    rows = kite.orders()
    if not isinstance(rows, list) or any(not isinstance(r, dict) or not r.get('order_id') for r in rows):
        raise ValueError('Invalid order book')
    return rows


def pending_order(kite, order_id):
    row = next((r for r in orderbook(kite) if str(r['order_id']) == order_id), None)
    if not row:
        bad('Order not found in your Kite account today.')
    if row.get('status') not in OPEN or row.get('variety') not in ('regular', 'amo'):
        bad('This order is no longer editable. Refresh its status.')
    if row.get('exchange') not in ('NSE', 'BSE') or row.get('product') not in ('CNC', 'MIS'):
        bad('Only regular/AMO cash equity orders can be managed here.')
    # Verify cash equity rather than an index or debt instrument.
    if not any(r['tradingsymbol'] == row.get('tradingsymbol') for r in instruments(kite, row['exchange'])):
        bad('Not a supported cash equity order.')
    return row


def signer():
    return URLSafeTimedSerializer(current_app.config['SECRET_KEY'], salt='manual-equity-review-v1')


def report(row):
    return {'request_id': row.id, 'state': row.status, 'order_id': row.order_id, 'message': row.message,
            'action': row.action, 'order': {k: v for k, v in json.loads(row.payload).items() if not k.startswith('_')}, 'created_at': row.created_at.isoformat() + 'Z'}


@bp.get('/api/equity/instruments')
@jwt_required()
@handled
def search():
    _, kite = connection()
    q = request.args.get('q', '').strip().upper()
    rows = instruments(kite, request.args.get('exchange', 'NSE'))
    matches = [r for r in rows if q and (q in r['tradingsymbol'] or q in (r['name'] or '').upper())]
    matches.sort(key=lambda r: (r['tradingsymbol'] != q, not r['tradingsymbol'].startswith(q), r['tradingsymbol']))
    return jsonify(ok=True, instruments=matches[:30])


@bp.get('/api/equity/orders')
@jwt_required()
@handled
@serialized_order
def orders():
    ue, kite = connection()
    rows = orderbook(kite)
    with SessionLocal() as db:
        intents = db.query(ManualEquityOrder).filter_by(user_id=ue.user_id).order_by(ManualEquityOrder.created_at.desc()).all()
        for intent in intents:
            if intent.status not in ('pending', 'unknown'):
                continue
            match = next((r for r in rows if (intent.action == 'place' and r.get('tag') == intent.tag)
                          or (intent.action != 'place' and str(r['order_id']) == intent.order_id)), None)
            p = json.loads(intent.payload)
            resolved = match and (intent.action == 'place' or
                       (intent.action == 'cancel' and match.get('status') in TERMINAL) or
                       (intent.action == 'modify' and all(match.get(k) == p.get(k) for k in
                         ('quantity', 'price', 'trigger_price', 'order_type', 'validity', 'disclosed_quantity'))))
            if resolved:
                intent.active_user = None
                intent.status, intent.order_id, intent.message = 'submitted', str(match['order_id']), 'Broker order found. Check its current status below.'
        db.commit()
        recent = [report(r) for r in intents[:50]]
        unresolved = [report(r) for r in intents if r.status in ('pending', 'unknown')]
    return jsonify(ok=True, orders=[{k: r.get(k) for k in FIELDS} for r in rows
                                   if r.get('exchange') in ('NSE', 'BSE') and r.get('product') in ('CNC', 'MIS')],
                   requests=recent, unresolved=unresolved, fetched_at=dt.datetime.now(dt.timezone.utc).isoformat())


@bp.post('/api/equity/preview')
@jwt_required()
@admin_required
@handled
@serialized_order
def preview():
    ue, kite = connection()
    live(ue)
    if unresolved_orders(ue.user_id):
        raise Conflict('An earlier order needs reconciliation. Refresh Orders and check Kite before continuing.')
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        bad('Request must be an object.')
    action = data.get('action', 'place')
    if action not in ('place', 'modify', 'cancel'):
        bad('Invalid order action.')
    order_id = str(data.get('order_id', ''))
    current = pending_order(kite, order_id) if action != 'place' else None
    if action == 'cancel':
        p = {k: current.get(k) for k in ('tradingsymbol', 'exchange', 'quantity', 'transaction_type', 'variety', 'product', 'order_type')}
    else:
        p = normalize(kite, data.get('order'))
        if current:
            for k in ('tradingsymbol', 'exchange', 'transaction_type', 'product', 'variety'):
                if p[k] != current[k]:
                    bad(f'Cannot modify {k}; cancel and create a new order instead.')
            if p['quantity'] <= current.get('filled_quantity', 0):
                bad('Total quantity must exceed the quantity already filled. Cancel to stop the remainder.')
    quote, margin = None, None
    if action != 'cancel':
        key = p['exchange'] + ':' + p['tradingsymbol']
        quote = kite.quote([key]).get(key)
        if (not isinstance(quote, dict) or not isinstance(quote.get('last_price'), (int, float))
                or not math.isfinite(quote['last_price']) or quote['last_price'] <= 0):
            bad('No broker quote available for this instrument.')
        if action == 'place':
            margin_fields = ('exchange', 'tradingsymbol', 'transaction_type', 'variety', 'product', 'order_type', 'quantity', 'price', 'trigger_price')
            estimate = kite.order_margins([{k: p[k] for k in margin_fields}])
            if not isinstance(estimate, list) or not estimate:
                raise ValueError('Missing margin estimate')
            margin = estimate[0].get('total')
            if not isinstance(margin, (int, float)) or not math.isfinite(margin) or margin < 0:
                raise ValueError('Invalid margin estimate')
        # Delivery sells must be backed by holdings or today's delivery buys.
        if p['transaction_type'] == 'SELL' and p['product'] == 'CNC':
            holdings = kite.holdings()
            positions = kite.positions()['net']
            held = sum(max(0, r.get('quantity', 0)) + max(0, r.get('t1_quantity', 0)) for r in holdings
                       if r.get('tradingsymbol') == p['tradingsymbol'])
            day = sum(max(0, r.get('quantity', 0)) for r in positions if r.get('tradingsymbol') == p['tradingsymbol'] and r.get('product') == 'CNC')
            reserved = sum(r.get('pending_quantity', r.get('quantity', 0)) for r in orderbook(kite)
                           if r.get('tradingsymbol') == p['tradingsymbol'] and r.get('product') == 'CNC'
                           and r.get('transaction_type') == 'SELL' and r.get('status') not in TERMINAL
                           and str(r.get('order_id')) != order_id)
            remaining = p['quantity'] - (current.get('filled_quantity', 0) if current else 0)
            if remaining > max(0, held + day - reserved):
                bad('Insufficient available delivery quantity after existing sell orders. Refresh your holdings and orders.')
    intent = {'id': uuid.uuid4().hex, 'uid': ue.user_id,
              'session': hashlib.sha256(kite.access_token.encode()).hexdigest(), 'action': action, 'order_id': order_id, 'order': p}
    return jsonify(ok=True, review_token=signer().dumps(intent), order=p, action=action,
                   last_price=quote.get('last_price') if quote else None,
                   quote_time=str(quote.get('timestamp') or quote.get('last_trade_time') or '') if quote else None,
                   margin=margin, filled_quantity=current.get('filled_quantity', 0) if current else 0,
                   expires_in=120)


@bp.post('/api/equity/execute')
@jwt_required()
@admin_required
@handled
@serialized_order
def execute():
    ue, kite = connection()
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        bad('Request must be an object.')
    token = data.get('review_token')
    try:
        # Verify signature first; already-processed tokens can be safely retried after expiry.
        intent = signer().loads(token)
        if intent['uid'] != ue.user_id:
            bad('This review belongs to another account.')
    except (BadSignature, TypeError):
        bad('Invalid review. Review your order again.')
    with SessionLocal() as db:
        existing = db.get(ManualEquityOrder, intent['id'])
        if existing:
            return jsonify(ok=True, **report(existing))
    try:
        signer().loads(token, max_age=120)
    except BadSignature:
        bad('Review expired. Review current prices before submitting again.')
    if intent.get('session') != hashlib.sha256(kite.access_token.encode()).hexdigest():
        raise Conflict('Kite connection changed. Review the order again for the current account.')
    live(ue)
    if unresolved_orders(ue.user_id):
        raise Conflict('An earlier order needs reconciliation. Refresh Orders before continuing.')
    action, p, order_id = intent['action'], intent['order'], intent['order_id']
    if action != 'place':
        current = pending_order(kite, order_id)
        if action == 'modify' and p['quantity'] <= current.get('filled_quantity', 0):
            bad('Order filled further since review. Refresh before modifying.')
    with SessionLocal() as db:
        row = ManualEquityOrder(id=intent['id'], user_id=ue.user_id, active_user=ue.user_id, action=action,
                               payload=json.dumps({**p, '_session': intent['session']}), tag='ax' + intent['id'][:18], order_id=order_id or None,
                               status='pending', message='Submission in progress. Do not submit another order.')
        db.add(row)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            existing = db.get(ManualEquityOrder, intent['id'])
            if existing:
                return jsonify(ok=True, **report(existing))
            raise Conflict('Another submission is in progress. Refresh Orders before continuing.')
    status, msg = 'submitted', 'Request accepted by Kite. Check order status for execution and fills.'
    try:
        if action == 'place':
            order_id = kite.place_order(**p, tag='ax' + intent['id'][:18])
        elif action == 'cancel':
            order_id = kite.cancel_order(variety=p['variety'], order_id=order_id)
        else:
            fields = ('quantity', 'price', 'trigger_price', 'order_type', 'validity', 'disclosed_quantity', 'market_protection')
            order_id = kite.modify_order(variety=p['variety'], order_id=order_id, **{k: p[k] for k in fields if k in p})
        if not order_id:
            raise ValueError('No order ID returned')
    except (InputException, OrderException, TokenException, PermissionException) as exc:
        status, msg = 'rejected', str(exc)[:500]
    except KiteException as exc:
        if exc.code in (400, 401, 403, 404, 405, 410, 422, 428, 429):
            status, msg = 'rejected', str(exc)[:500]
        else:
            status, msg = 'unknown', 'Broker response was not confirmed. Refresh Orders and check Kite before continuing.'
    except Exception:
        status, msg = 'unknown', 'Broker response was not confirmed. Do not repeat this trade. Refresh Orders to reconcile, or check Kite.'
    with SessionLocal() as db:
        row = db.get(ManualEquityOrder, intent['id'])
        row.status, row.message = status, msg
        row.active_user = ue.user_id if status == 'unknown' else None
        row.order_id = str(order_id) if order_id else None
        db.commit()
        return jsonify(ok=True, **report(row))
