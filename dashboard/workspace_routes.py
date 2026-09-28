"""Validated chart preferences stored per account, independent of browser storage."""
import json
from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required, get_jwt_identity
from db.database import SessionLocal
from db.models import ChartWorkspace
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
            panes = [{'symbol': '', 'interval': 'day'} for _ in range(4)]
            return jsonify(ok=True, saved=False, workspace={'count': 1, 'arrangement':'grid', 'days':'all', 'panes':panes})
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
            if not isinstance(pane,dict) or not isinstance(pane.get('symbol'),str) or len(pane['symbol'].strip())>100 or pane.get('interval') not in INTERVALS:
                bad('Each chart requires a supported timeframe and a symbol of at most 100 characters.')
            clean.append({'symbol':pane['symbol'].strip().upper(),'interval':pane['interval']})
        payload=json.dumps({'count':value['count'],'arrangement':value['arrangement'],'days':value['days'],'panes':clean})
        if row: row.payload=payload
        else: db.add(ChartWorkspace(user_id=uid,payload=payload))
        db.commit()
        return jsonify(ok=True)


@workspace_bp.route('/api/workspace/annotations', methods=['GET', 'PUT'])
@jwt_required()
def chart_annotations():
    import math
    import re
    from db.models import ChartAnnotation
    context = request.args.get('context', '').strip()
    if not context or len(context) > 200:
        bad('A chart symbol and timeframe are required.')
    with SessionLocal() as db:
        row = db.get(ChartAnnotation, (int(get_jwt_identity()), context))
        if request.method == 'GET':
            return jsonify(ok=True, layout=json.loads(row.payload) if row else None)
        value = request.get_json(silent=True)
        if not isinstance(value, dict) or len(json.dumps(value)) > 500_000:
            bad('Chart settings must be an object smaller than 500 KB.')
        studies, drawings = value.get('studies'), value.get('drawings')
        kinds = {'SMA', 'EMA', 'WMA', 'Bollinger', 'Donchian', 'VWAP', 'RSI', 'MACD', 'Stochastic', 'ATR', 'CCI', 'ROC', 'OBV', 'Volume'}
        def number(v):
            return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
        def color(v):
            return isinstance(v, str) and re.fullmatch(r'#[0-9a-fA-F]{6}', v)
        def point(p):
            if not isinstance(p, dict) or not number(p.get('price')) or not number(p.get('offset', 0)):
                return False
            t = p.get('time')
            if number(t):
                return True
            if isinstance(t, dict):
                try:
                    import datetime
                    datetime.date(t['year'], t['month'], t['day'])
                    return True
                except (KeyError, TypeError, ValueError):
                    return False
            if isinstance(t, str):
                try:
                    import datetime
                    datetime.date.fromisoformat(t)
                    return True
                except ValueError:
                    pass
            return False
        if not isinstance(studies, list) or len(studies) > 8 or not isinstance(drawings, list) or len(drawings) > 300:
            bad('Save up to eight indicators and 300 drawings.')
        ids = set()
        for study in studies:
            if (not isinstance(study, dict) or not isinstance(study.get('kind'), str) or study.get('kind') not in kinds
                    or type(study.get('period')) is not int or not 1 <= study['period'] <= 500
                    or not color(study.get('color'))):
                bad('Invalid indicator settings.')
        for drawing in drawings:
            if not isinstance(drawing, dict) or not color(drawing.get('color')):
                bad('Invalid drawing.')
            if drawing.get('kind') == 'brush':
                points = drawing.get('points')
                if not isinstance(points, list) or not 2 <= len(points) <= 5000 or not all(point(p) for p in points):
                    bad('Invalid brush points.')
            elif not isinstance(drawing.get('kind'), str) or drawing.get('kind') not in {'trend', 'ray', 'horizontal', 'vertical', 'rectangle', 'ellipse', 'fib', 'measure', 'text'} or not point(drawing.get('a')) or not point(drawing.get('b')):
                bad('Invalid drawing coordinates.')
            if not isinstance(drawing.get('text', ''), str) or len(drawing.get('text', '')) > 80:
                bad('Drawing notes must be at most 80 characters.')
        for item in studies + drawings:
            if type(item.get('id')) is not int or item['id'] <= 0 or item['id'] in ids:
                bad('Invalid chart object identifier.')
            ids.add(item['id'])
        payload = json.dumps({'studies': studies, 'drawings': drawings})
        if row:
            row.payload = payload
        else:
            db.add(ChartAnnotation(user_id=int(get_jwt_identity()), context=context, payload=payload))
        db.commit()
        return jsonify(ok=True)
