"""UTC four-hour derivation of confirmed1H inputs; never fill missing candles."""
import csv,hashlib,io,json,sys
from decimal import Decimal
from datetime import datetime,timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parent;BASE='data/derived_4h_v1';HOUR=3600000
FIELDS=['symbol','open_utc_ms','close_utc_ms','open','high','low','close','volume_base','input_sha256','input_available_utc','first_generated_utc','usable_from_utc','source']
def utc(s):
    x=datetime.fromisoformat(s.replace('Z','+00:00'))
    if x.tzinfo is None:raise ValueError('timezone required')
    return x.astimezone(timezone.utc)
def digest(x):return hashlib.sha256(json.dumps(x,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def aggregate(rows,now,previous=None):
    previous=previous or {};by={};groups={}
    for row in rows:
        s=row['symbol'];tick=int(row['open_utc_ms'])
        if s not in ('DOGE-USDT','BTC-USDT','ETH-USDT') or tick%HOUR or (s,tick) in by:raise ValueError('invalid symbol/alignment or duplicate1H')
        values={k:Decimal(row[k]) for k in ('open','high','low','close','volume_base')}
        if any(not v.is_finite() for v in values.values()) or min(values[k] for k in ('open','high','low','close'))<=0 or values['volume_base']<0:raise ValueError('invalid candle numbers')
        if values['low']>min(values['open'],values['close']) or values['high']<max(values['open'],values['close']) or values['low']>values['high']:raise ValueError('invalid OHLC bounds')
        if 'confirmed' not in row['source'] or utc(row['first_ingested_utc'])>now:raise ValueError('unconfirmed/future source visibility')
        if tick+HOUR>int(now.timestamp()*1000):continue
        by[s,tick]=row;groups.setdefault(s,[]).append(tick//(4*HOUR)*(4*HOUR))
    bars=[];missing=[]
    for s,starts in sorted(groups.items()):
        for start in range(min(starts),max(starts)+4*HOUR,4*HOUR):
            expected=[start+i*HOUR for i in range(4)];absent=[t for t in expected if (s,t) not in by]
            if absent:
                reason='not_closed_yet' if start+4*HOUR>int(now.timestamp()*1000) else 'leading_boundary' if start==min(starts) else 'missing_closed_children'
                missing.append({'symbol':s,'open_utc_ms':start,'missing_open_utc_ms':absent,'reason':reason});continue
            children=[by[s,t] for t in expected];key=s+'|'+str(start);sha=digest(children)
            old=previous.get(key)
            if old and old['input_sha256']!=sha:raise ValueError('previous confirmed4H input changed; require reviewed version')
            first=old['first_generated_utc'] if old else now.isoformat()
            available=max(utc(r['first_ingested_utc']) for r in children)
            close=datetime.fromtimestamp((start+4*HOUR)/1000,timezone.utc)
            bars.append({'symbol':s,'open_utc_ms':str(start),'close_utc_ms':str(start+4*HOUR),
                'open':children[0]['open'],'high':str(max(Decimal(r['high']) for r in children)),
                'low':str(min(Decimal(r['low']) for r in children)),'close':children[-1]['close'],
                'volume_base':str(sum((Decimal(r['volume_base']) for r in children),Decimal(0))),
                'input_sha256':sha,'input_available_utc':available.isoformat(),'first_generated_utc':first,
                'usable_from_utc':max(available,close,utc(first)).isoformat(),'source':'derived UTC4H from four confirmed OKX1H; not independent information'})
    return bars,missing
def run(root=ROOT):
    now=datetime.now(timezone.utc);path=root/BASE/'ohlc.csv';previous={}
    if path.exists():
        with path.open(encoding='utf-8',newline='') as f:previous={r['symbol']+'|'+r['open_utc_ms']:r for r in csv.DictReader(f)}
    source=root/'data/hourly_research/ohlc.csv';raw=source.read_bytes()
    bars,missing=aggregate(list(csv.DictReader(io.StringIO(raw.decode('utf-8-sig')))),now,previous)
    current={r['symbol']+'|'+r['open_utc_ms'] for r in bars}
    if not set(previous)<=current:raise ValueError('derived history truncation')
    out=io.StringIO(newline='');writer=csv.DictWriter(out,fieldnames=FIELDS,lineterminator='\n');writer.writeheader();writer.writerows(bars)
    path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(out.getvalue().encode())
    report={'schema_version':1,'status':'ok_with_missing_buckets' if missing else 'ok','generated_at_utc':now.isoformat(),
        'source_file':source.relative_to(root).as_posix(),'source_sha256':hashlib.sha256(raw).hexdigest(),
        'complete_4h_bars':len(bars),'per_symbol':{s:sum(r['symbol']==s for r in bars) for s in ('DOGE-USDT','BTC-USDT','ETH-USDT')},
        'missing_buckets':missing,'missing_bucket_count':len(missing),'interior_closed_missing_count':sum(r['reason']=='missing_closed_children' for r in missing),
        'historical_pit_before_first_generation_verified':False,
        'volume_unit':'base asset units, summed exactly; never mix DOGE/BTC/ETH or contract units','no_missing_fill':True,'no_trading':True}
    (root/BASE/'latest.json').write_bytes((json.dumps(report,indent=2)+'\n').encode());return report
if __name__=='__main__':
    try:
        r=run();print(json.dumps({k:r[k] for k in ('status','complete_4h_bars','missing_bucket_count')}))
    except Exception as e:
        (ROOT/BASE).mkdir(parents=True,exist_ok=True);(ROOT/BASE/'failure_latest.json').write_text(json.dumps({'status':'BLOCKED','error':str(e)}),encoding='utf-8');raise
