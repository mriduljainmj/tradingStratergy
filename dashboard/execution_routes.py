"""Read and explicitly reconcile orders whose broker result was uncertain."""
import datetime
import json
import hashlib
from flask import Blueprint, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity
from dashboard.authz import admin_required
from core.engine_pool import engine_pool
from db.database import SessionLocal
from db.models import ExecutionIncident, ManualEquityOrder
from execution.order_safety import serialized_order

execution_bp = Blueprint('execution_safety', __name__)


@execution_bp.get('/api/execution/incidents')
@jwt_required()
def incidents():
    with SessionLocal() as db:
        rows = db.query(ExecutionIncident).filter(
            ExecutionIncident.user_id == int(get_jwt_identity()),
            ExecutionIncident.status.in_(('pending', 'needs_review')),
        ).order_by(ExecutionIncident.id.desc()).all()
        data = [dict(id=r.id, symbol=r.symbol, side=r.side,
                     quantity=r.quantity, order_id=r.order_id, status=r.status,
                     created_at=r.created_at.isoformat()) for r in rows]
        manual = db.query(ManualEquityOrder).filter(
            ManualEquityOrder.user_id == int(get_jwt_identity()),
            ManualEquityOrder.status.in_(('pending', 'unknown'))).all()
        for r in manual:
            order = json.loads(r.payload)
            data.append(dict(id='equity-' + r.id, symbol=order['tradingsymbol'],
                             side=r.action + ' ' + order['transaction_type'], quantity=order['quantity'],
                             order_id=r.order_id, status=r.status, created_at=r.created_at.isoformat()))
        return jsonify(ok=True, data=data)


@execution_bp.post('/api/execution/reconcile')
@jwt_required()
@admin_required
@serialized_order
def reconcile():
    uid = int(get_jwt_identity())
    ue = engine_pool.get_or_create(uid)
    if ue.state.kite_auth_error or not ue.broker.kite.access_token:
        return jsonify(ok=False, error='Connect Kite to reconcile orders.'), 409
    try:
        # Never infer a flat account from an incomplete/failed broker response.
        positions = ue.broker.kite.positions()
        orders = ue.broker.kite.orders()
        if not isinstance(positions, dict) or not isinstance(positions.get('net'), list) or not isinstance(orders, list):
            return jsonify(ok=False, error='Broker status could not be verified.'), 502
        if any(not isinstance(p, dict) or p.get('quantity') != 0 for p in positions['net']):
            return jsonify(ok=False, error='Close outstanding broker positions in Kite before reconciling.'), 409
        terminal = {'COMPLETE', 'REJECTED', 'CANCELLED'}
        if any(not isinstance(o, dict) or o.get('status') not in terminal for o in orders):
            return jsonify(ok=False, error='Wait for or cancel outstanding orders in Kite first.'), 409
        ue.stop()
        with SessionLocal() as db:
            pending = db.query(ExecutionIncident).filter(
                ExecutionIncident.user_id == uid,
                ExecutionIncident.status.in_(('pending', 'needs_review')),
            ).all()
            manual = db.query(ManualEquityOrder).filter(
                ManualEquityOrder.user_id == uid,
                ManualEquityOrder.status.in_(('pending', 'unknown'))).all()
            if any((datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None) - r.created_at).total_seconds() < 120 for r in manual):
                return jsonify(ok=False, error='Wait two minutes after an uncertain manual submission, then refresh Orders before reconciling.'), 409
            if any(json.loads(r.payload).get('_session') != hashlib.sha256(ue.broker.kite.access_token.encode()).hexdigest() for r in manual):
                return jsonify(ok=False, error='The Kite session changed since this uncertain order. Refresh Equity Orders to match its broker record; otherwise a broker-history audit is required.'), 409
            pending += manual
            if not pending:
                return jsonify(ok=True, reconciled=0)
            # Old-day attempts require operator audit; today's empty order book
            # cannot establish what happened on a previous trading day.
            today = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=5, minutes=30))).date()
            order_map = {str(o.get('order_id')): o for o in orders}
            for attempt in pending:
                if attempt.created_at.replace(tzinfo=datetime.timezone.utc).astimezone(datetime.timezone(datetime.timedelta(hours=5, minutes=30))).date() != today:
                    return jsonify(ok=False, error='An older order needs a broker-history audit before it can be cleared.'), 409
                if attempt.order_id and attempt.order_id not in order_map:
                    return jsonify(ok=False, error='The recorded order is missing from the broker order book.'), 409
            for attempt in pending:
                attempt.status = 'reconciled'
                if isinstance(attempt, ManualEquityOrder):
                    attempt.active_user = None
                    attempt.message = 'Operator reconciled against a flat broker account; review Kite fills before a new order.'
            db.commit()
        ue.equity_engines.clear()
        ue._engine = None
        ue.state.reset('PAPER')
        ue.state.trades_enabled = False
        ue.state.status = 'Reconciled — review/sync fills before enabling trading'
        return jsonify(ok=True, reconciled=len(pending))
    except Exception:
        return jsonify(ok=False, error='Unable to verify broker status. No order was marked reconciled.'), 502
