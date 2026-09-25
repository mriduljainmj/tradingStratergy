"""Fail closed on ambiguous orders. Never fabricate fills or retry an unknown order."""
import math
import threading
from functools import wraps
from db.database import SessionLocal
from db.models import ExecutionIncident


class ExecutionBlocked(RuntimeError):
    pass


def unresolved_orders(user_id):
    with SessionLocal() as db:
        return db.query(ExecutionIncident).filter(
            ExecutionIncident.user_id == user_id,
            ExecutionIncident.status.in_(('pending', 'needs_review')),
        ).count() > 0


_order_lock = threading.RLock()


def serialized_order(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        with _order_lock:
            return fn(*args, **kwargs)
    return wrapped


@serialized_order
def confirmed_order(broker, user_id, symbol, side, quantity, *, equity=False, strategy_id=None):
    # Commit intent first. A process crash or lost response is visible on restart.
    with SessionLocal() as db:
        if unresolved_orders(user_id):
            raise ExecutionBlocked('An earlier order needs broker reconciliation.')
        attempt = ExecutionIncident(user_id=user_id, strategy_id=strategy_id,
                                    symbol=symbol, side=side, quantity=quantity,
                                    status='pending')
        db.add(attempt)
        db.commit()
        attempt_id = attempt.id
    try:
        place = broker.place_equity_market_order if equity else broker.place_market_order
        order_id = place(symbol, side, quantity)
        if not order_id:
            raise ExecutionBlocked('Broker did not return an order ID.')
        with SessionLocal() as db:
            db.get(ExecutionIncident, attempt_id).order_id = str(order_id)
            db.commit()
        fill = broker.get_fill_price(order_id)
        if not fill or not math.isfinite(float(fill)) or fill <= 0:
            raise ExecutionBlocked('Fill not confirmed. Check the order in Kite before resuming.')
        with SessionLocal() as db:
            record = db.get(ExecutionIncident, attempt_id)
            record.status = 'complete'
            record.fill_price = float(fill)
            db.commit()
        return float(fill)
    except Exception as exc:
        with SessionLocal() as db:
            record = db.get(ExecutionIncident, attempt_id)
            record.status = 'needs_review'
            record.message = str(exc)[:500]
            db.commit()
        raise ExecutionBlocked('Order status needs review in Kite. Automatic execution is paused.') from exc


def has_exposure(ue):
    if not ue:
        return False
    if getattr(getattr(ue, '_engine', None), 'execution_blocked', False):
        return True
    if getattr(getattr(ue, '_engine', None), 'strategy', None) and ue._engine.strategy.in_position:
        return True
    return any(e.in_pos or getattr(e, 'execution_blocked', False)
               for e in getattr(ue, 'equity_engines', {}).values())


def verify_flat_broker(broker):
    """Reject fresh live starts when broker exposure is unknown or unmanaged."""
    positions = broker.kite.positions()
    orders = broker.kite.orders()
    if not isinstance(positions, dict) or not isinstance(positions.get('net'), list) or not isinstance(orders, list):
        raise ExecutionBlocked('Broker positions/orders could not be verified. Live start blocked.')
    if any(not isinstance(p, dict) or 'quantity' not in p or p['quantity'] != 0 for p in positions['net']):
        raise ExecutionBlocked('Existing broker positions must be reviewed and closed before a fresh live start.')
    if any(not isinstance(o, dict) or o.get('status') not in {'COMPLETE', 'CANCELLED', 'REJECTED'} for o in orders):
        raise ExecutionBlocked('Pending broker orders must be resolved before a fresh live start.')
