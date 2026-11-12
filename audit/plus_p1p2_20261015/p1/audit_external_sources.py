"""Read current local source bytes only; no collection, publication, or ledger writes."""
import csv,hashlib,json,sys
from collections import Counter
from datetime import datetime,timezone,timedelta
from pathlib import Path
root=Path.cwd();sys.path.insert(0,str(root));now=datetime.now(timezone.utc)
out=root/'audit/plus_p1p2_20261015/p1';items={};inputs={}
def load(path):
    raw=(root/path).read_bytes();inputs[path]=hashlib.sha256(raw).hexdigest()
    return list(csv.DictReader(raw.decode('utf-8-sig').splitlines()))
def stamp(v):
    if not v:return None
    if isinstance(v,(int,float)) or str(v).isdigit():return datetime.fromtimestamp(float(v)/1000,timezone.utc)
    d=datetime.fromisoformat(v.replace('Z','+00:00'))
    return d.replace(tzinfo=timezone.utc) if len(v)==10 else d

def summarize(name,rows,key,timestamp,seen=None,daily=False):
    keys=[tuple(r[k] for k in key) for r in rows];times=[stamp(r[timestamp]) for r in rows if r.get(timestamp)]
    available=[stamp(r[seen]) for r in rows if seen and r.get(seen)]
    days={x.date() for x in times};expected=(max(days)-min(days)).days+1 if days and daily else None
    items[name]={'rows':len(rows),'key_fields':key,'duplicate_keys':len(keys)-len(set(keys)),
       'timestamp_field':timestamp,'min_timestamp':min(times).isoformat() if times else None,'max_timestamp':max(times).isoformat() if times else None,
       'invalid_or_future_timestamp_count':sum(x.tzinfo is None or x>now for x in times),
       'first_seen_field':seen,'first_seen_min':min(available).isoformat() if available else None,'first_seen_max':max(available).isoformat() if available else None,
       'availability_before_event_count':sum(stamp(r[seen])<stamp(r[timestamp]) for r in rows if seen and r.get(seen) and r.get(timestamp)),
       'missing_value_counts':{field:sum(r.get(field) in (None,'') for r in rows) for field in sorted({k for r in rows for k in r}) if any(r.get(field) in (None,'') for r in rows)},
       'coverage_numerator':len(days) if daily else None,'coverage_denominator':expected,'missing_daily_dates':expected-len(days) if expected else None,
       'cadence_limit':'daily calendar dates within observed span' if daily else 'unknown; no cadence inferred for event streams/business calendars/snapshots',
       'public_pre_target_witness_replayed':False,'production_admission':'BLOCKED'}
    return items[name]
summarize('onchain_daily_activity',load('data/onchain/daily_activity.csv'),['date_utc'],'date_utc',daily=True)
summarize('onchain_daily_snapshots',load('data/onchain/daily.csv'),['observed_at_utc'],'observed_at_utc','observed_at_utc')
summarize('whale_live_events',load('data/whales/events.csv'),['txid'],'time_seen_utc','first_observed_utc')
summarize('whale_confirmed_events',load('data/whales_blocks/events.csv'),['txid'],'observed_at_utc','observed_at_utc')
summarize('address_balance_observations',load('data/okx_address_balances/observations.csv'),['cycle','address'],'observed_at_utc','observed_at_utc')
summarize('liquidation_events',load('data/liquidations/events.csv'),['event_key'],'event_ts_ms','received_at_utc')
summarize('liquidity_snapshots',load('data/liquidity/hourly_snapshots.csv'),['collected_at_ms','instrument'],'collected_at_ms','collected_at_ms')
macro=load('data/macro/observations.csv')
for sid in sorted({r['series_id'] for r in macro}):
    rows=[r for r in macro if r['series_id']==sid]
    summarize('macro_'+sid,rows,['series_id','observation_date'],'observation_date','first_seen_utc')
    items['macro_'+sid]['frequency']=rows[0]['frequency']
    items['macro_'+sid]['release_vintages_before_first_seen_verified']=False
import derivatives_pit_v1 as derivatives
import enhancement_sources as enhancement
for name,module,path,field in [('derivatives',derivatives,derivatives.LEDGER,'kind'),('enhancement',enhancement,enhancement.BASE+'/observations.jsonl','source_id')]:
    raw=(root/path).read_bytes();inputs[path]=hashlib.sha256(raw).hexdigest();error=None
    try:rows=module.read(root);verified=True
    except Exception as exc:
        rows=[json.loads(line) for line in raw.decode().splitlines()];verified=False;error=type(exc).__name__+': '+str(exc)
    for sid in sorted({r[field] for r in rows}):
        group=[r for r in rows if r[field]==sid]
        event='provider_ts_utc' if name=='derivatives' and group[0].get('provider_ts_utc') else 'first_seen_utc'
        s=summarize(name+'_'+sid,group,['capture_id'] if name=='derivatives' else ['record_sha256'],event,'first_seen_utc')
        s.update(chain_and_raw_digest_verified=verified,guard_error=error)
        if name=='derivatives':
            coverage=derivatives.coverage(rows,sid,now)
            s.update(coverage_numerator=round(coverage*24),coverage_denominator=24,missing_closed_hour_slots=24-round(coverage*24),cadence_limit='existing derivatives.coverage: preceding24 closed UTC receipt-hour buckets',usable_from_min=min(r['usable_from_utc'] for r in group),usable_from_max=max(r['usable_from_utc'] for r in group))
        else:
            s['distinct_observation_keys']=len({r['observation_key'] for r in group});s['revision_records']=len(group)-s['distinct_observation_keys'];s['observation_key_min']=min(r['observation_key'] for r in group);s['observation_key_max']=max(r['observation_key'] for r in group)
            s['historical_release_time_independently_verified']=False
# Independently replay derivative packets even when committed-prefix guard blocks.
rows=[json.loads(line) for line in (root/derivatives.LEDGER).read_text().splitlines()]
previous=None; replay_errors=[]
for row in rows:
    try:
        checked=dict(row);record=checked.pop('record_sha256')
        assert derivatives.digest(checked)==record and row['previous_sha256']==previous
        raw=(root/row['raw_path']).read_bytes();assert hashlib.sha256(raw).hexdigest()==row['raw_sha256']
        parsed=derivatives.parse(row['kind'],raw,row['requested_at_utc'],row['first_seen_utc'])
        assert all(row.get(k)==v for k,v in parsed.items())
    except Exception as exc:replay_errors.append(type(exc).__name__+': '+str(exc))
    previous=row['record_sha256']
for kind in ('OI','FUNDING'):
    items['derivatives_'+kind]['independent_local_chain_raw_formula_replay_verified']=not replay_errors
    items['derivatives_'+kind]['independent_replay_errors']=replay_errors
# Explicit missing calendar dates; not an event-feed schedule claim.
dates={stamp(r['date_utc']).date() for r in load('data/onchain/daily_activity.csv')}
day=min(dates); missing=[]
while day<=max(dates):
    if day not in dates:missing.append(day.isoformat())
    day+=timedelta(days=1)
items['onchain_daily_activity']['missing_dates']=missing
for name,path in [('onchain','data/onchain/metrics_latest.json'),('whale_scan','data/whales_blocks/latest.json'),('address_scan','data/okx_address_balances/checkpoint.json'),('liquidation_window','data/liquidations/latest.json')]:
    raw=(root/path).read_bytes();inputs[path]=hashlib.sha256(raw).hexdigest();items[name+'_reported_scope']={'snapshot':json.loads(raw),'independently_recollected':False}
result={'audited_at_utc':now.isoformat(),'mode':'CURRENT_LOCAL_BYTES_READ_ONLY_NO_COLLECTION','sources':items,'inputs_sha256':inputs,'production_changed':False,'public_witness_replayed':False,'coverage_unknown_is_not_complete':True}
(out/'external_source_audit.json').write_text(json.dumps(result,ensure_ascii=False,sort_keys=True,indent=2)+'\n',encoding='utf-8')
print(json.dumps({k:v for k,v in items.items() if 'reported_scope' not in k},ensure_ascii=False))
