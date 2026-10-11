#!/usr/bin/env python3
"""Independent source freshness/coverage audit. Do not conflate staleness and predictive validity."""
import json,sys,os,csv,math
from decimal import Decimal
from datetime import datetime,timezone,timedelta,date
from pathlib import Path
BASE=Path(__file__).resolve().parent
OUT=BASE/'data/quality/latest.json'

FEEDS={
 'market':('data/latest.json','collected_at_utc',3,True,('ok',)),
 'onchain':('data/onchain/latest.json','observed_at_utc',36,True,('ok',)),
 'liquidations_okx':('data/liquidations/latest.json','ended_at_utc',3,True,('ok','partial')),
 'liquidity':('data/liquidity/latest.json','collected_at_utc',3,True,('ok',)),
 'etf':('data/etf/latest.json','ended_at_utc',96,True,('ok',)),
 'macro':('data/macro/latest.json','ended_at_utc',48,True,('ok',)),
 'whale_sse_optional':('data/whales/latest.json','observed_to_utc',8,False,('ok','partial')),
 'whale_confirmed_blocks':('data/whales_blocks/latest.json','ended_at_utc',2,True,('ok',)),
 'onchain_daily_activity':('data/onchain/metrics_latest.json','collected_at_utc',36,False,('ok',)),
 'okx_por_balances_optional':('data/okx_address_balances/latest.json','ended_at_utc',4,False,('ok',)),
 'bybit_liquidations_optional':('data/bybit_liquidations/latest.json','ended_at_utc',8,False,('ok','partial')),
 'binance_liquidations_optional':('data/binance_liquidations/latest.json','ended_at_utc',8,False,('ok','partial')),
}
# 1h history expected even if research backfill not yet complete.
ADDITIONAL_HOURLY='data/hourly_research/latest.json'

# Data-quality checks: event IDs, UTC timestamps, row counts, and no fabricated liquidation totals.
STAGE2_CSVS={
 'liquidations_okx':('data/liquidations/events.csv','event_key','event_ts_ms','received_at_utc','event_rows_total'),
 'binance_liquidations_optional':('data/binance_liquidations/events.csv','event_key','event_ts_ms','observed_at_utc','stored_unique_snapshots'),
 'bybit_liquidations_optional':('data/bybit_liquidations/events.csv','event_key','event_ts_ms','observed_at_utc','stored_unique_events'),
 'liquidity':('data/liquidity/hourly_snapshots.csv','collected_at_ms','collected_at_ms',None,'data_quality.rows'),
}
def check_stage2_csvs(base=BASE, now=None):
 if now is None:now=datetime.now(timezone.utc)
 checks={}; failures=[]
 for key,(file,id_field,ts_field,utc_field,count_field) in STAGE2_CSVS.items():
  result={'file':file,'status':'error','rows':0,'duplicates':0,'invalid_timestamps':0,'unsorted_timestamps':0,'issues':[]}
  try:
   with (Path(base)/file).open(newline='',encoding='utf-8-sig') as stream:
    rd=csv.DictReader(stream);columns=rd.fieldnames or []
    required={id_field,ts_field}
    if utc_field: required.add(utc_field)
    if key=='liquidity':required.update(('instrument','best_bid','best_ask','liquidation_usd','liquidation_source_status'))
    if not required.issubset(columns):raise ValueError('missing required CSV columns')
    seen=set();last=None;count=0
    for row in rd:
     count+=1
     event_id=row[id_field]
     if not event_id:raise ValueError('empty event ID or collected timestamp')
     if event_id in seen:result['duplicates']+=1
     seen.add(event_id)
     raw=row[ts_field]
     if not raw or not raw.isdigit():raise ValueError('noninteger millisecond timestamp')
     tick=int(raw)
     if tick<1262304000000 or tick>int((now.timestamp()+900)*1000):
      result['invalid_timestamps']+=1
     if last is not None and tick<last:result['unsorted_timestamps']+=1
     last=tick
     if utc_field:
      stamp=datetime.fromisoformat((row.get(utc_field) or '').replace('Z','+00:00'))
      if stamp.tzinfo is None:raise ValueError('timestamp lacks UTC offset')
      if abs((stamp-datetime.fromtimestamp(tick/1000,timezone.utc)).total_seconds())>600:
       result['invalid_timestamps']+=1
     else:
      if row['instrument']!='DOGE-USDT-SWAP':raise ValueError('instrument changed')
      bid=float(row['best_bid']);ask=float(row['best_ask'])
      if not (math.isfinite(bid) and math.isfinite(ask) and 0<bid<ask):
       raise ValueError('crossed/invalid order book')
      if (row['liquidation_usd'] or '').strip() or row['liquidation_source_status']!='not_available':
       raise ValueError('fabricated liquidation amount or unverified liquidation source')
    result['rows']=count
   snapshot=json.loads((Path(base)/FEEDS[key][0]).read_text(encoding='utf-8'))
   expected=snapshot
   for part in count_field.split('.'):expected=expected[part]
   if int(expected)!=count:result['issues'].append('latest_snapshot_row_count_mismatch')
   if result['duplicates']:result['issues'].append('duplicate_event_ids')
   if result['invalid_timestamps']:result['issues'].append('invalid_or_misaligned_timestamps')
   if result['unsorted_timestamps']:result['issues'].append('unsorted_timestamps')
   result['status']='ok' if not result['issues'] else 'invalid'
  except (OSError,KeyError,ValueError,TypeError,OverflowError,json.JSONDecodeError) as exc:
   result['issues'].append(type(exc).__name__+': '+str(exc)[:150])
  if result['status']!='ok':failures.append(key)
  checks[key]=result
 return checks,failures


def check_etf_csv(base=BASE,now=None):
 """Validate observed BTC/ETH ETF rows; history availability is first_seen, never trade date."""
 if now is None:now=datetime.now(timezone.utc)
 out={'state':'error','rows':0,'per_asset':{},'duplicate_keys':0,'timestamp_errors':0,
      'point_in_time_historical_backfill_verified':False,'trade_date_as_available_timestamp_allowed':False,
      'revision_history_before_first_seen_available':False,'issues':[]}
 try:
  root=Path(base)/'data/etf'
  meta=json.loads((root/'latest.json').read_text(encoding='utf-8'))
  if meta.get('status')!='ok':raise ValueError('ETF source status not ok')
  with (root/'daily_flows.csv').open(newline='',encoding='utf-8-sig') as file:
   rd=csv.DictReader(file)
   fields={'asset','trade_date_us','total_usd_m','reported_funds','funds_with_values','source_url','first_seen_utc','last_checked_utc','revision_count'}
   if not fields.issubset(rd.fieldnames or []):raise ValueError('ETF daily CSV missing columns')
   rows=list(rd)
  with (root/'revisions.csv').open(newline='',encoding='utf-8-sig') as file:
   rd=csv.DictReader(file)
   if not {'asset','trade_date_us','old_total_usd_m','new_total_usd_m','observed_at_utc'}.issubset(rd.fieldnames or []):
    raise ValueError('ETF revision CSV missing columns')
   revisions=list(rd)
  seen=set();assets={'BTC':[],'ETH':[]};rev_counts={}
  for rev in revisions:
   key=(rev['asset'],rev['trade_date_us'])
   if rev['asset'] not in assets:raise ValueError('invalid ETF revision asset')
   for field in ('old_total_usd_m','new_total_usd_m'):
    if not Decimal(rev[field]).is_finite():raise ValueError('nonfinite ETF revision')
   stamp=datetime.fromisoformat(rev['observed_at_utc'].replace('Z','+00:00'))
   if stamp.tzinfo is None or stamp>now+timedelta(minutes=15):raise ValueError('invalid ETF revision timestamp')
   rev_counts[key]=rev_counts.get(key,0)+1
  all_keys=[]
  for row in rows:
   asset=row['asset']
   if asset not in assets:raise ValueError('non-BTC/ETH ETF asset')
   day=date.fromisoformat(row['trade_date_us'])
   if day.weekday()>=5 or day>now.date():raise ValueError('invalid ETF trading day')
   key=(asset,row['trade_date_us'])
   if key in seen:out['duplicate_keys']+=1
   seen.add(key);all_keys.append((row['trade_date_us'],asset))
   if not Decimal(row['total_usd_m']).is_finite():raise ValueError('nonfinite ETF amount')
   reported=int(row['reported_funds']);filled=int(row['funds_with_values'])
   if not(0<filled<=reported):raise ValueError('ETF constituent fund count invalid')
   if row['source_url']!=('https://farside.co.uk/'+asset.lower()+'/'):raise ValueError('unknown ETF table source')
   first=datetime.fromisoformat(row['first_seen_utc'].replace('Z','+00:00'))
   last=datetime.fromisoformat(row['last_checked_utc'].replace('Z','+00:00'))
   if first.tzinfo is None or last.tzinfo is None or first>last or last>now+timedelta(minutes=15) or first.date()<day:
    out['timestamp_errors']+=1
   if int(row['revision_count'])!=rev_counts.get(key,0):
    raise ValueError('ETF revision count mismatch')
   assets[asset].append(row)
  if all_keys!=sorted(all_keys):raise ValueError('ETF rows not sorted by date and asset')
  if out['duplicate_keys'] or out['timestamp_errors']:raise ValueError('ETF duplicate key or invalid timestamps')
  if len(rows)!=meta['stored_rows']:raise ValueError('ETF stored_rows mismatch')
  if set(rev_counts)-seen:raise ValueError('revision exists without ETF base row')
  for asset,items in assets.items():
   if not items:raise ValueError(asset+' ETF absent')
   dates=[row['trade_date_us'] for row in items]
   if max(dates)!=meta['latest_trade_date'][asset]:raise ValueError(asset+' latest date mismatch')
   out['per_asset'][asset]={'rows':len(items),'first_trade_date':min(dates),'last_trade_date':max(dates),
    'first_collection_time_utc':min(r['first_seen_utc'] for r in items),
    'missing_constituent_fund_values':sum(int(r['funds_with_values'])<int(r['reported_funds']) for r in items)}
  out['rows']=len(rows);out['revisions']=len(revisions)
  out['state']='ok'
  out['limitations']=['US ETF market holidays and publication delays: omitted dates are NOT zero netflows',
   'Rows first collected after the trade date cannot be backdated for predictive backtesting',
   'Farside is a third-party table, not audited original issuer cash flows',
   'Limited observed date span is not complete ETF history']
 except (OSError,ValueError,KeyError,TypeError,ArithmeticError,OverflowError,json.JSONDecodeError) as exc:
  out['issues'].append(type(exc).__name__+': '+str(exc)[:180])
 return out

def evaluate(base=BASE,now=None):
 if now is None:now=datetime.now(timezone.utc)
 if now.tzinfo is None:raise ValueError('timezone required')
 reports={};critical=[];warnings=[]
 for name,(file,field,limit,required,allowed) in FEEDS.items():
  row={'source_file':file,'age_limit_hours':limit,'required':required,
      'coverage_note':'Source health only: NOT proof of completeness, entity labels or causal predictive power'}
  try:
   x=json.loads((Path(base)/file).read_text(encoding='utf-8'))
   state=x.get('status');row['source_status']=state
   if state not in allowed:raise ValueError('status='+str(state))
   raw=x.get(field)
   if not isinstance(raw,str):raise ValueError('timestamp absent: '+field)
   stamp=datetime.fromisoformat(raw.replace('Z','+00:00'))
   if stamp.tzinfo is None:raise ValueError('timezone absent')
   age=(now-stamp).total_seconds()/3600
   row['age_hours']=round(age,3)
   if age<-.25 or age>limit:raise ValueError('stale/clock-skewed: '+str(round(age,2))+' h')
   if name=='etf':
    dates=x.get('latest_trade_date',{})
    if any(not isinstance(dates.get(s),str) or len(dates[s])!=10 for s in ('BTC','ETH')):
     raise ValueError('ETF trade dates missing')
   if name=='liquidations_okx' and 'NOT market-wide totals' not in x.get('coverage',''):
    raise ValueError('liquidation coverage disclaimer missing')
   if name=='whale_confirmed_blocks':
    if x.get('backlog_blocks_at_run_end')!=0 or x.get('errors'):
     raise ValueError('whale block scanner unfinished or error')
   row['state']='ok_coverage_limited' if state=='partial' or 'optional' in name or 'whale' in name or 'liquidations' in name else 'ok'
  except (OSError,ValueError,TypeError,KeyError,json.JSONDecodeError) as e:
   row['state']='stale_or_failed';row['error']=str(e)[:190]
   (critical if required else warnings).append(name)
  reports[name]=row
 try:
  hourly=json.loads((Path(base)/ADDITIONAL_HOURLY).read_text(encoding='utf-8'))
  latest=hourly['quality']['symbols']['DOGE-USDT']['last_confirmed_start_utc']
  candle_start=datetime.fromisoformat(latest.replace('Z','+00:00'))
  age=(now-candle_start).total_seconds()/3600
  if hourly.get('status') not in ('ok','partial_backfill') or age>3 or age<1 or hourly['quality']['symbols']['DOGE-USDT']['missing_hours_between_endpoints']!=0:
   raise ValueError('hourly incomplete/stale '+str(round(age,2))+' h')
  reports['hourly_research']={'state':'ok_coverage_limited','last_candle_utc':latest,'candle_start_age_hours':round(age,3),
   'historical_backfill_complete':hourly.get('archive_complete',False),'required':False}
 except (OSError,ValueError,TypeError,KeyError,json.JSONDecodeError) as e:
  reports['hourly_research']={'state':'stale_or_failed','required':False,'error':str(e)[:190]}
  warnings.append('hourly_research')
 stage2_files,stage2_failed=check_stage2_csvs(base,now)
 reports['stage2_csv_quality']={'state':'ok' if not stage2_failed else 'stale_or_failed','checks':stage2_files,'failed_sources':stage2_failed,'required':True}
 if stage2_failed:critical.append('stage2_csv_quality')
 etf_quality=check_etf_csv(base,now)
 reports['stage3_etf_csv_quality']={**etf_quality,'required':True}
 if etf_quality['state']!='ok':critical.append('stage3_etf_csv_quality')
 obj={'schema_version':3,'audited_at_utc':now.isoformat(),
  'status':'blocked' if critical else ('ok_with_source_warnings' if warnings else 'ok_with_coverage_limits'),
  'critical_failures':critical,'optional_source_warnings':warnings,'sources':reports,
  'verified_exchange_netflow':False,'verified_global_liquidations':False,
  'stage2_liquidation_time_coverage_complete':False,
  'stage3_etf_historical_point_in_time_verified':False,
  'model_predictive_edge':'NO_VALIDATED_EDGE',
  'google_sheets_collector_verified_by_this_audit':False,
  'limitations':['Old SSE and confirmed-block whale collection are separate feeds',
   'An old SSE sample may be stale while confirmed block scanning remains current',
   'Partial Bybit/Binance sampling is not all-exchange coverage',
   'GitHub statuses are not proof of Apps Script trigger, execution and Sheets readback']}
 return obj

def check(base=None,now=None):
 result=evaluate(base or BASE,now)
 for name,record in result['sources'].items():
  print(record['state'].upper(),name,record.get('age_hours',''),record.get('error',''))
 print(result['status'],'critical:',result['critical_failures'],'optional:',result['optional_source_warnings'])
 return result['critical_failures']

def main():
 try:o=evaluate()
 except Exception as e:o={'schema_version':2,'audited_at_utc':datetime.now(timezone.utc).isoformat(),
  'status':'blocked','critical_failures':['auditor_exception'],'error':type(e).__name__+': '+str(e)[:250]}
 OUT.parent.mkdir(parents=True,exist_ok=True)
 p=OUT.with_suffix('.json.tmp');p.write_text(json.dumps(o,indent=2,ensure_ascii=False)+'\n');os.replace(p,OUT)
 print(json.dumps({k:o.get(k) for k in ('status','critical_failures','optional_source_warnings','error')},ensure_ascii=False))
 return int(o['status']=='blocked')

if __name__=='__main__':sys.exit(main())
