"""Read-only asset boundary and source audits. Git hashes are not external WORM."""
import csv
import hashlib
import json
import math
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

SYMBOLS=('DOGE-USDT','BTC-USDT','ETH-USDT')


def utc(value):
    value=datetime.fromisoformat(value.replace('Z','+00:00'))
    if value.tzinfo is None or value.utcoffset()!=timedelta(0):
        raise ValueError('explicit UTC required')
    return value


def sha(raw):return hashlib.sha256(raw).hexdigest()


def safe(root,path):
    target=(root/path).resolve()
    if not target.is_relative_to(root.resolve()) or target.is_symlink():
        raise ValueError('path outside asset root')
    return target


def manifest(root,paths,commit):
    return {'source_commit':commit,'algorithm':'sha256_exact_checkout_bytes',
            'external_immutable_retention_verified':False,
            'assets':{p:sha(safe(root,p).read_bytes()) for p in sorted(paths)}}


def verify(root,record):
    for path,expected in record['assets'].items():
        if sha(safe(root,path).read_bytes())!=expected:
            raise ValueError('protected asset changed: '+path)
    return {'protected_files':len(record['assets']),'status':'UNCHANGED'}


def catalog(items):
    if len(items)!=36 or {r['id'] for r in items}!=set(range(1,37)):
        raise ValueError('36 unique catalog IDs required')
    counts=Counter(r['decision'] for r in items)
    if not set(counts)<=set(('ADOPT_GOVERNANCE','RESEARCH','MONITOR','STOP')):
        raise ValueError('unknown governance decision')
    return dict(counts)


def market(path,step,now):
    if now.tzinfo is None:raise ValueError('aware audit clock required')
    by={};keys=set();source=set()
    with path.open(encoding='utf-8-sig',newline='') as stream:
        for r in csv.DictReader(stream):
            symbol=r['symbol'];stamp=int(r['open_utc_ms']);key=(symbol,stamp)
            if symbol not in SYMBOLS or stamp<0 or stamp%step or key in keys:
                raise ValueError('duplicate/invalid symbol or timestamp: '+str(key))
            keys.add(key)
            o,hi,lo,c,v=(float(r[k]) for k in ('open','high','low','close','volume_base'))
            if (not all(math.isfinite(x) for x in (o,hi,lo,c,v)) or min(o,lo,c)<=0
                    or v<0 or hi<max(o,lo,c) or lo>min(o,c)):
                raise ValueError('invalid OHLCV')
            end=stamp+step
            if end>int(now.timestamp()*1000):raise ValueError('unclosed/future candle')
            expected='OKX history-candles '+('1H' if step==3600000 else '1Dutc')+' confirmed'
            if r['source']!=expected:raise ValueError('unconfirmed source')
            seen=int(utc(r['first_ingested_utc']).timestamp()*1000) if 'first_ingested_utc' in r else None
            if seen is not None and (seen<end or seen>int(now.timestamp()*1000)):
                raise ValueError('invalid ingestion chronology')
            source.add(r['source']);by.setdefault(symbol,{})[stamp]=(c,v,seen)
    if not by:raise ValueError('empty market data')
    for symbol,bars in by.items():
        times=sorted(bars)
        if any(b-a!=step for a,b in zip(times,times[1:])):raise ValueError('missing bars: '+symbol)
    shared=set.intersection(*(set(bars) for bars in by.values()))
    if not shared or any(max(bars)!=max(shared) for bars in by.values()):
        raise ValueError('cross-asset latest timestamp mismatch')
    times=sorted(shared)
    data={'times':times}
    for symbol,bars in by.items():
        data[symbol]={'close':[bars[t][0] for t in times], 'volume':[bars[t][1] for t in times],
                      'seen':[bars[t][2] for t in times]}
    report={'path':path.name,'sha256':sha(path.read_bytes()),'rows':len(keys),
            'duplicate_keys':0,'missing_internal_bars':0,'source':sorted(source),
            'symbols':{s:{'bars':len(v),'first_open_utc':datetime.fromtimestamp(min(v)/1000,timezone.utc).isoformat(),
                          'last_closed_utc':datetime.fromtimestamp((max(v)+step)/1000,timezone.utc).isoformat()}
                       for s,v in by.items()},
            'historical_download_is_prospective_evidence':False}
    return data,report


def ledgers(root,now):
    forecasts={};scores={}
    for name,target in [('forward_registry.csv',forecasts),('forward_scores.csv',scores)]:
        with (root/'data/direction_v42'/name).open(encoding='utf-8',newline='') as stream:
            for r in csv.DictReader(stream):
                key=r['forecast_id']
                if not key or key in target:raise ValueError('duplicate/empty ledger event')
                target[key]=r
    for key,r in forecasts.items():
        issued=utc(r['issued_at_utc']);end=utc(r['target_utc']);h=int(r['horizon_hours'])
        if end!=issued+timedelta(hours=h) or issued>now or utc(r['source_quote_utc'])>issued:
            raise ValueError('invalid forecast chronology')
    for key,r in scores.items():
        if key not in forecasts:raise ValueError('orphan score')
        if utc(r['observed_at_utc'])>now:raise ValueError('future score')
        if r['status']=='scored' and (not r['target_quote_utc'] or
                utc(r['target_quote_utc'])<utc(forecasts[key]['target_utc'])):
            raise ValueError('target price before target')
    due={key for key,r in forecasts.items() if utc(r['target_utc'])+timedelta(minutes=25)<=now}
    return {'forecasts':len(forecasts),'score_receipts':len(scores),
            'duplicate_event_ids':0,'missing_score_receipts':len(set(forecasts)-set(scores)),
            'due_forecasts':len(due),'missing_matured_score_receipts':len(due-set(scores)),
            'not_due_forecasts':len(forecasts)-len(due),
            'score_status_counts':dict(Counter(r['status'] for r in scores.values())),
            'trust_limit':'local ledger structure check; official V2 raw/public witness guard remains authoritative'}


def audit(root,now):
    hourly,hr=market(root/'data/hourly_research/ohlc.csv',3600000,now)
    _,dr=market(root/'data/history/daily.csv',86400000,now)
    policy=json.loads((root/'config/factor_eligibility_v1.json').read_bytes())
    official=json.loads((root/'data/stage5/forward_validation_v2/latest.json').read_bytes())
    return hourly,{'audited_at_utc':now.isoformat(),'hourly':hr,'daily':dr,
        'catalog_counts':catalog(policy['legacy36']),'catalog_sha256':sha((root/policy['catalog_path']).read_bytes()),
        'catalog_current_google_sheet_reverified':False,'ledger':ledgers(root,now),
        'official_snapshot':{'audited_at_utc':official['audited_at_utc'],'horizons':official['horizons'],
                            'snapshot_raw_and_remote_witness_replayed_by_this_audit':False},
        'production_model_changed':False,'validated_70pct':False}
