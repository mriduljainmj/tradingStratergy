"""Authenticated SSE bridge; Kite credentials never reach the browser."""
import json
import time
import uuid
import threading

from flask import Blueprint, Response, jsonify, request, stream_with_context
from flask_jwt_extended import get_jwt, jwt_required
from dashboard.routes import _ue
from execution.market_stream import MarketStream

stream_bp = Blueprint('market_stream', __name__)
_catalog_lock = threading.Lock()


def register_chart_token(ue, token):
    with _catalog_lock:
        now = time.time()
        entries = getattr(ue, '_chart_stream_tokens', {})
        entries = {k:v for k,v in entries.items() if now-v < 86400}
        entries[int(token)] = now
        ue._chart_stream_tokens = dict(sorted(entries.items(), key=lambda x: x[1])[-3000:])


def register_portfolio_tokens(ue, tokens):
    with _catalog_lock:
        ue._portfolio_stream_tokens = ({t for t in tokens if isinstance(t, int) and not isinstance(t, bool) and t > 0}, time.time())


@stream_bp.get('/api/market-stream')
@jwt_required()
def market_stream():
    ue = _ue()
    kite = ue.broker.kite
    if ue.state.kite_auth_error or not kite.access_token:
        return jsonify(ok=False, error='Connect Kite to stream market data.'), 409
    try:
        tokens = {int(t) for t in request.args.get('tokens', '').split(',') if t}
        portfolio = request.args.get('scope') == 'portfolio'
        limit = 500 if portfolio else 32
        if not tokens or len(tokens) > limit or any(t <= 0 for t in tokens):
            raise ValueError(f'Choose between 1 and {limit} instruments per view.')
        # Chart tokens are registered only after an authenticated server-side lookup.
        with _catalog_lock:
            registered = {t for t, stamp in getattr(ue, '_chart_stream_tokens', {}).items() if time.time()-stamp < 86400}
        if portfolio:
            with _catalog_lock:
                owned, stamp = getattr(ue, '_portfolio_stream_tokens', (set(), 0))
            if time.time() - stamp > 120 or not tokens <= owned:
                raise ValueError('Refresh your portfolio to subscribe to its instruments.')
        extra = set() if portfolio else tokens - {256265, 260105, 265} - registered
        if extra:
            allowed = {i['instrument_token'] for i in ue.broker.get_nfo_instruments()
                       if i.get('name') == 'NIFTY' and i.get('instrument_type') in ('CE', 'PE')}
            if not extra <= allowed:
                raise ValueError('Unsupported chart instrument.')
        with ue._lifecycle_lock:
            feed = getattr(ue, '_market_stream', None)
            credentials = (kite.api_key, kite.access_token)
            if not feed or feed.closed or feed.credentials != credentials:
                if feed:
                    feed.close()
                feed = ue._market_stream = MarketStream(*credentials)
            client = str(uuid.uuid4())
            feed.attach(client, tokens)
    except ValueError as exc:
        return jsonify(ok=False, error=str(exc)), 400
    except Exception:
        return jsonify(ok=False, error='Kite streaming is unavailable. Reconnect Kite and retry.'), 502
    # Reauthenticate each minute (including expiry and token revocation checks).
    deadline = min(time.time() + 60, get_jwt()['exp'])
    @stream_with_context
    def events():
        try:
            initial = True
            while time.time() < deadline and not feed.closed:
                if (ue.broker.kite.api_key, ue.broker.kite.access_token) != feed.credentials:
                    break
                yield 'data: ' + json.dumps(feed.snapshot(tokens, tail=None if initial else 2, quotes_only=portfolio)) + '\n\n'
                initial = False
                time.sleep(.5)
        finally:
            feed.detach(client)
    return Response(events(), mimetype='text/event-stream', headers={
        'Cache-Control': 'no-cache, no-store', 'X-Accel-Buffering': 'no'})
