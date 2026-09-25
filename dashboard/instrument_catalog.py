"""Public NSE metadata cached in the database; no broker credentials are stored here."""
import datetime
import json
import threading
from db.database import SessionLocal
from db.models import InstrumentCatalog

_lock = threading.Lock()


def catalog(broker):
    today=datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=5,minutes=30))).date()
    with _lock, SessionLocal() as db:
        row=db.get(InstrumentCatalog,'NSE')
        if row and row.fetched_on==today:
            return json.loads(row.payload), False
        try:
            source=broker.kite.instruments('NSE')
            if not isinstance(source,list) or not source:
                raise ValueError('Empty instrument catalogue')
            records=[{'symbol':r['tradingsymbol'],'name':r.get('name') or r['tradingsymbol'],'exchange':'NSE','instrument_type':r.get('instrument_type'),'token':r.get('instrument_token')} for r in source if r.get('tradingsymbol') and r.get('instrument_type')=='EQ']
            if not records: raise ValueError('No equity instruments returned')
        except Exception:
            return (json.loads(row.payload) if row else []), True
        if row:
            row.payload=json.dumps(records);row.fetched_on=today
        else: db.add(InstrumentCatalog(exchange='NSE',fetched_on=today,payload=json.dumps(records)))
        db.commit()
        return records, False
