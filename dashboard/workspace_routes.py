"""Validated chart preferences stored per account, independent of browser storage."""
import json
from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required, get_jwt_identity
from db.database import SessionLocal
from db.models import ChartWorkspace, Watchlist
from config.settings import TradingConfig
from dashboard.api_support import bad

workspace_bp = Blueprint('workspace', __name__)
INTERVALS = ('minute', '5minute', '15minute', '60minute', 'day', 'week')


@workspace_bp.route('/api/workspace/charts', methods=['GET', 'PUT'])
@jwt_required()
def chart_workspace():
    uid = int(get_jwt_identity())
    with SessionLocal() as db:
        row = db.get(ChartWorkspace, uid)
        if request.method == 'GET':
            if row:
                return jsonify(ok=True, workspace=json.loads(row.payload), saved=True)
            primary = TradingConfig().index_symbol.split(':')[-1]
            symbols = list(dict.fromkeys([primary] + [w.symbol for w in db.query(Watchlist).filter_by(user_id=uid).order_by(Watchlist.id).limit(4)]))
            panes = [{'symbol': symbols[i % len(symbols)], 'interval': 'day'} for i in range(4)]
            return jsonify(ok=True, saved=False, workspace={'count': 2, 'arrangement':'grid', 'days':'all', 'panes':panes})
        value = request.get_json()
        if isinstance(value.get('count'), bool) or value.get('count') not in (1,2,4):
            bad('Choose one, two or four charts.')
        if value.get('arrangement') not in ('grid','stack') or isinstance(value.get('days'),bool) or value.get('days') not in (3,7,30,90,'all'):
            bad('Invalid chart arrangement or history range.')
        panes = value.get('panes')
        if not isinstance(panes,list) or len(panes)!=4:
            bad('Four chart pane settings are required.')
        clean=[]
        for pane in panes:
            if not isinstance(pane,dict) or not isinstance(pane.get('symbol'),str) or not 1<=len(pane['symbol'].strip())<=100 or pane.get('interval') not in INTERVALS:
                bad('Each chart requires a symbol and supported timeframe.')
            clean.append({'symbol':pane['symbol'].strip().upper(),'interval':pane['interval']})
        payload=json.dumps({'count':value['count'],'arrangement':value['arrangement'],'days':value['days'],'panes':clean})
        if row: row.payload=payload
        else: db.add(ChartWorkspace(user_id=uid,payload=payload))
        db.commit()
        return jsonify(ok=True)
