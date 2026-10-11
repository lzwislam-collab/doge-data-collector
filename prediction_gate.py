#!/usr/bin/env python3
"""Fail-closed DOGE predictive claim gate. Research-only; does not alter DCA."""
import json,os
from pathlib import Path
from datetime import datetime,timezone,date
ROOT=Path(__file__).resolve().parent
OUT=ROOT/'data'/'prediction_gate'/'latest.json'
def load(rel):
 p=ROOT/rel
 if not p.exists():raise ValueError('missing '+rel)
 j=json.loads(p.read_text(encoding='utf-8'))
 if j.get('status')!='ok':raise ValueError(rel+' is not ok')
 return j

def stage2_admissibility():
 """Fail closed: market-wide liquidation totals do not exist in partial free feeds."""
 out={'liquidation_24h_totals_allowed':False,'liquidation_24h_directional_imbalance_allowed':False,
      'missing_notification_window_equals_zero':False,'liquidation_event_samples_research_only':False,
      'swap_liquidity_snapshots_research_only':False,'retrospective_model_training_authorized':False,
      'reason':'Partial public notice windows cannot establish all-market liquidation totals',
      'source_coverage':'unverified','predictive_edge_established':False}
 try:
  quality=load('data/quality/latest.json')
  liq=load('data/liquidations/latest.json')
  book=load('data/liquidity/latest.json')
  checks=quality['sources']['stage2_csv_quality']
  qc=checks.get('checks',{})
  events_ok=checks.get('state')=='ok' and all(qc.get(k,{}).get('status')=='ok'
       for k in ('liquidations_okx','binance_liquidations_optional','bybit_liquidations_optional'))
  out['liquidation_event_samples_research_only']=bool(events_ok and liq.get('not_market_wide') is True
       and liq.get('historical_backfill') is False)
  out['swap_liquidity_snapshots_research_only']=bool(checks.get('state')=='ok'
       and qc.get('liquidity',{}).get('status')=='ok' and book.get('status')=='ok'
       and book.get('recent_trades',{}).get('coverage_note') and
       book.get('liquidations',{}).get('liquidation_usd') is None)
  out['source_coverage']='partial_480_seconds_per_900_seconds_approx' if out['liquidation_event_samples_research_only'] else 'unverified'
  out['allowed_snapshot_features']=['spread_bps','bid_depth_usdt_10bps','ask_depth_usdt_10bps',
      'open_interest_usd','funding_rate'] if out['swap_liquidity_snapshots_research_only'] else []
  out['restrictions']=['Only observations at collection timestamp, never hindsight full-day totals',
      'No missing-as-zero liquidation values, no synthetic OI-as-liquidation',
      'Snapshot availability and true collection timestamp must be aligned before any backtest',
      'Permitted for descriptive research only: no model predictive-edge claim']
 except Exception as exc:
  out['source_coverage']='invalid_or_unavailable'
  out['audit_error']=type(exc).__name__+': '+str(exc)[:200]
 return out

def main():
 stamp=datetime.now(timezone.utc).isoformat()
 result={'schema_version':1,'evaluated_at_utc':stamp,'status':'error',
         'claim_permission':'BLOCKED','research_only':True,'does_not_change_dca':True,
         'model_edge_proven':False,'checks':{},'errors':[],
         'stage2_feature_admissibility':stage2_admissibility()}
 try:
  val=load('data/validation/latest.json')
  stat=load('data/stat_audit/latest.json')
  prob=load('data/prob_baseline/latest.json')
  end_dates=[val['end_utc'],stat['last_date_utc'],prob['data_end_utc']]
  result['checks']['date_consistency']={'pass':len(set(end_dates))==1,'dates':end_dates}
  result['checks']['data_quality']={'pass':val['missing_internal_days']==0 and val['duplicate_keys']==0,
         'doge_days':val['doge_days'],'missing_days':val['missing_internal_days'],'duplicate_keys':val['duplicate_keys']}
  result['checks']['aligned_days']={'pass':stat['aligned_days']==prob['aligned_days']==val['doge_days']}
  now_date=datetime.now(timezone.utc).date()
  stale=(now_date-date.fromisoformat(min(end_dates))).days
  result['checks']['freshness']={'pass':stale<=3 and stale>=1,'days_since_last_confirmed_daily_bar':stale}
  horizon_checks={}
  for h in ('1d','7d','30d'):
   z=stat['horizons'][h]
   n=int(z['nonoverlapping']['n'])
   ci=z['paired_block_bootstrap']['bootstrap_95pct_interval_pp']
   excludes=bool(z['paired_block_bootstrap']['bootstrap_interval_excludes_zero'])
   if len(ci)!=2 or ci[0]>ci[1]:raise ValueError('invalid confidence interval '+h)
   horizon_checks[h]={'nonoverlap_n':n,'min_effective_n_threshold':100,
       'confidence_interval_pp':ci,'confidence_interval_excludes_zero':excludes,
       'validated_edge':n>=100 and excludes}
  result['checks']['prediction_horizons']=horizon_checks
  quality=all(v['pass'] for k,v in result['checks'].items() if k!='prediction_horizons')
  edge=quality and any(v['validated_edge'] for v in horizon_checks.values())
  result['status']='ok' if quality else 'data_quality_blocked'
  result['claim_permission']='REVIEW_REQUIRED' if edge else 'NO_VALIDATED_EDGE'
  result['model_edge_proven']=False # Separate genuine prospective holdout required.
  result['limitations']=['A significant historical CI alone cannot authorize live forecast marketing',
    'No true prospective registered predictions, trading costs, execution or repeated-hypothesis adjustment verified',
    'Partial liquidation feeds are never allowed as 24h/global liquidation totals',
    'No change to DOGE investment execution rules']
 except Exception as e:result['errors'].append(type(e).__name__+': '+str(e)[:250])
 OUT.parent.mkdir(parents=True,exist_ok=True)
 temp=OUT.with_suffix('.json.tmp')
 temp.write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
 os.replace(temp,OUT)
 print(json.dumps({'status':result['status'],'claim_permission':result['claim_permission'],'errors':result['errors']}))
 return 0 if result['status']=='ok' else 1
if __name__=='__main__':raise SystemExit(main())
