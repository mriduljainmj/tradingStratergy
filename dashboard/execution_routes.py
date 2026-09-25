"""Read and explicitly reconcile orders whose broker result was uncertain."""
import datetime
from flask import Blueprint, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity
from dashboard.authz import admin_required
from core.engine_pool import engine_pool
from db.database import SessionLocal
from db.models import ExecutionIncident

execution_bp = Blueprint('execution_safety', __name__)


@execution_bp.get('/api/execution/incidents')
@jwt_required()
def incidents():
    with SessionLocal() as db:
        rows = db.query(ExecutionIncident).filter(
            ExecutionIncident.user_id == int(get_jwt_identity()),
            ExecutionIncident.status.in_(('pending', 'needs_review')),
        ).order_by(ExecutionIncident.id.desc()).all()
        return jsonify(ok=True, data=[dict(id=r.id, symbol=r.symbol, side=r.side,
                         quantity=r.quantity, order_id=r.order_id, status=r.status,
                         created_at=r.created_at.isoformat()) for r in rows])


@execution_bp.post('/api/execution/reconcile')
@jwt_required()
@admin_required
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
        if any(p.get('quantity', 0) != 0 for p in positions['net']):
            return jsonify(ok=False, error='Close outstanding broker positions in Kite before reconciling.'), 409
        terminal = {'COMPLETE', 'REJECTED', 'CANCELLED'}
        if any(o.get('status') not in terminal for o in orders):
            return jsonify(ok=False, error='Wait for or cancel outstanding orders in Kite first.'), 409
        ue.stop()
        with SessionLocal() as db:
            pending = db.query(ExecutionIncident).filter(
                ExecutionIncident.user_id == uid,
                ExecutionIncident.status.in_(('pending', 'needs_review')),
            ).all()
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
            db.commit()
        ue.equity_engines.clear()
        ue._engine = None
        ue.state.reset('PAPER')
        ue.state.trades_enabled = False
        ue.state.status = 'Reconciled — review/sync fills before enabling trading'
        return jsonify(ok=True, reconciled=len(pending))
    except Exception:
        return jsonify(ok=False, error='Unable to verify broker status. No order was marked reconciled.'), 502
