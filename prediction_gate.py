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
  quality=json.loads((ROOT/'data/quality/latest.json').read_text(encoding='utf-8'))
  if quality.get('status') not in ('ok_with_coverage_limits','ok_with_source_warnings') or quality.get('critical_failures'):
   raise ValueError('data/quality/latest.json has critical failure')
  liq=json.loads((ROOT/'data/liquidations/latest.json').read_text(encoding='utf-8'))
  if (liq.get('status') not in ('ok','partial') or liq.get('data_quality',{}).get('errors')
      or liq.get('subscription_coverage_pct',0)<85):
   raise ValueError('partial liquidation observation coverage insufficient or failed')
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


def stage3_etf_admissibility():
 """ETF snapshots known only at first_seen_utc, never retroactively on trade date."""
 out={'historical_trade_date_as_available_at_allowed':False,
      'pre_collection_backtest_using_etf_values_allowed':False,
      'retrospective_model_training_authorized':False,
      'historical_revisions_point_in_time_available':False,
      'snapshot_research_only':False,
      'publication_timestamp_rule':'first_seen_utc or verified later publication timestamp, NEVER trade_date_us',
      'predictive_edge_established':False}
 try:
  quality=json.loads((ROOT/'data/quality/latest.json').read_text(encoding='utf-8'))
  gate=quality['sources']['stage3_etf_csv_quality']
  if quality.get('critical_failures') or gate['state']!='ok':
   raise ValueError('ETF CSV integrity failed')
  snap=load('data/etf/latest.json')
  out['snapshot_research_only']=bool(snap.get('unit')=='USD millions' and
      set(gate['per_asset'])=={'BTC','ETH'} and gate.get('rows',0)>0)
  out['observed_rows_by_asset']={k:v['rows'] for k,v in gate['per_asset'].items()}
  out['coverage_note']='Limited historical window; US trading sessions only; Farside third-party aggregated totals'
 except Exception as exc:
  out['audit_error']=type(exc).__name__+': '+str(exc)[:180]
 return out


def stage4_macro_policy_admissibility():
 """No FRED observation-date lookahead, no BLS schedule-as-release, no retrospective regulatory publication."""
 out={'historical_macro_observation_as_release_timestamp_allowed':False,
      'unverified_historical_macro_vintages_allowed':False,
      'calendar_schedule_as_actual_cpi_ppi_release_allowed':False,
      'retrospective_sec_cftc_pubdate_backtest_allowed':False,
      'official_macro_values_historical_backtest_authorized':False,
      'policy_headline_price_direction_validated':False,
      'macro_first_seen_research_only':False,
      'policy_event_first_seen_research_only':False,
      'rule':'Use actual observed first_seen_utc or separately verified release/vintage availability, never observation_date'}
 try:
  quality=json.loads((ROOT/'data/quality/latest.json').read_text(encoding='utf-8'))
  if quality.get('critical_failures'):raise ValueError('source quality has critical failures')
  stage=quality['sources']['stage4_macro_policy_csv_quality']
  if stage.get('state')!='ok':raise ValueError('stage4 quality not verified')
  src=stage['checks']
  out['macro_first_seen_research_only']=src['fred_macro']['state']=='ok'
  out['policy_event_first_seen_research_only']=all(src[k]['state']=='ok'
     for k in ('fed_bls_policy_calendar','cftc_policy_candidates','sec_policy_candidates'))
  out['audited_macro_observations']=src['fred_macro']['rows']
  out['audited_policy_candidates_and_calendar_rows']=sum(src[k]['rows']
     for k in ('fed_bls_policy_calendar','cftc_policy_candidates','sec_policy_candidates'))
  out['limitations']=['FRED observation dates precede publication; revised values are latest-vintage rather than historical PIT',
   'BLS calendar event time is a scheduled future release, not confirmation of actual data publication',
   'SEC/CFTC keyword candidates are incomplete and no causal bullish/bearish tags are established',
   'Use first_seen_utc conservatively until release timestamps, prior vintages and revision timelines are independently validated']
 except Exception as exc:
  out['audit_error']=type(exc).__name__+': '+str(exc)[:180]
 return out

def main():
 stamp=datetime.now(timezone.utc).isoformat()
 result={'schema_version':1,'evaluated_at_utc':stamp,'status':'error',
         'claim_permission':'BLOCKED','research_only':True,'does_not_change_dca':True,
         'model_edge_proven':False,'checks':{},'errors':[],
         'stage2_feature_admissibility':stage2_admissibility(),
         'stage3_etf_admissibility':stage3_etf_admissibility(),
         'stage4_macro_policy_admissibility':stage4_macro_policy_admissibility()}
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
