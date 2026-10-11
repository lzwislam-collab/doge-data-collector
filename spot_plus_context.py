#!/usr/bin/env python3
"""Five-pillar, PIT-aware context for DOGE Spot Prediction Plus.

This is an integrated evidence inventory, NOT an unvalidated signal combiner.
No trading recommendation, numerical sentiment score, or hindsight ETF/macro
feature is inferred. Original 36-item audit is a governance reference, not
a claim each factor has a real-time and causal trading signal.
"""
import csv,json,math
from datetime import datetime,timedelta,timezone
from pathlib import Path

def utc(value):
 d=datetime.fromisoformat(str(value).replace('Z','+00:00'))
 if d.tzinfo is None or d.utcoffset()!=timedelta(0):
  raise ValueError('non-UTC source timestamp')
 return d.astimezone(timezone.utc)

def load(root,path):
 file=root/path
 if not file.exists():return None
 value=json.loads(file.read_text(encoding='utf-8'))
 if not isinstance(value,dict):raise ValueError('invalid source '+path)
 return value

def view_source(root,path,timefield,now,limit_hours,allowed=('ok',),status_field='status'):
 src=load(root,path)
 if src is None:return {'state':'missing','source_file':path,'allowed_for_model':False}
 status=src.get(status_field)
 stamp=src.get(timefield)
 if not stamp:raise ValueError('missing report timestamp '+path)
 when=utc(stamp)
 age=(now-when).total_seconds()/3600
 if age < -0.1:raise ValueError('future report timestamp '+path)
 state='current' if age<=limit_hours else 'stale'
 if status not in allowed:state='partial' if status in ('partial','ok_sample_complete') else 'unhealthy'
 return {'state':state,'status':status,'source_file':path,
  'source_utc':when.isoformat(),'age_hours':round(age,2),'allowed_for_model':False}

def read_latest_etfs(root,now):
 path=root/'data/etf/daily_flows.csv'
 if not path.exists():return {'state':'missing','available_for_historical_training':False}
 latest={}
 with path.open(newline='',encoding='utf-8-sig') as f:
  r=csv.DictReader(f)
  need={'asset','trade_date_us','total_usd_m','first_seen_utc'}
  if not need.issubset(r.fieldnames or []):raise ValueError('ETF column mismatch')
  seen=set()
  for row in r:
   asset=row['asset']
   if asset not in ('BTC','ETH'):raise ValueError('unexpected ETF asset')
   key=(asset,row['trade_date_us'])
   if key in seen:raise ValueError('duplicate ETF trade date')
   seen.add(key)
   first=utc(row['first_seen_utc'])
   if first>now+timedelta(minutes=5):raise ValueError('future ETF first seen time')
   amount=float(row['total_usd_m'])
   if not math.isfinite(amount):raise ValueError('bad ETF amount')
   old=latest.get(asset)
   if old is None or row['trade_date_us']>old['trade_date_us']:
    latest[asset]={'trade_date_us':row['trade_date_us'],
     'net_flow_m_usd':round(amount,3),'first_seen_utc':first.isoformat()}
 return {'state':'snapshot_research_only','latest_by_asset':latest,
  'available_for_historical_training':False,
  'PIT_rule':'Never use ETF trade date as its availability timestamp; historical pre-first_seen backtest prohibited'}

def inspect_legacy36(root):
 path=root/'data/stage5/spot_plus/legacy36_map.csv'
 summary={'retained_or_combined':21,'background_governance':8,'uncalibrated_withdrawn':7}
 if not path.exists():
  return {'prior_audit_source':'reports/DOGE_SYSTEM_AUDIT_2026-10-11.md',
    'historical_summary_only':summary,'item_level_current_mapping_reconciled':False,
    'original_google_sheet_full_catalog_not_validated_here':True,
    'no_claim_all_36_are_signal_features':True}
 with path.open(newline='',encoding='utf-8-sig') as stream:
  reader=csv.DictReader(stream)
  need={'Source36#','Legacy item','Decision','New section','Audit note','Version'}
  if not need.issubset(reader.fieldnames or []):
   raise ValueError('original 36 mapping missing columns')
  data=list(reader)
 if len(data)!=36 or [str(i) for i in range(1,37)]!=[x['Source36#'] for x in data]:
  raise ValueError('original 36 mapping has missing duplicate or unsorted entries')
 counts={k:sum(x['Decision']==k for x in data)
   for k in ('KEEP_MERGE','BACKEND_ONLY','REMOVE_SCORE')}
 if counts!={'KEEP_MERGE':21,'BACKEND_ONLY':8,'REMOVE_SCORE':7}:
  raise ValueError('original 36 mapping category counts have changed')
 if any(not x['Legacy item'] or not x['New section'] for x in data):
  raise ValueError('original 36 item name or section missing')
 return {'prior_audit_source':'reports/DOGE_SYSTEM_AUDIT_2026-10-11.md',
   'original_source':'DOGE 长期统计底表 v0.1 / Forecast Report Slim Audit',
   'item_level_snapshot':'data/stage5/spot_plus/legacy36_map.csv',
   'last_verified_against_sheet_utc_day':'2026-10-11',
   'snapshot_not_automatically_synced_to_sheet':True,
   'historical_summary_only':summary,
   'item_level_current_mapping_reconciled':True,
   'original_google_sheet_full_catalog_not_validated_here':False,
   'no_claim_all_36_are_signal_features':True}


def inspect(root,now):
 if now.utcoffset()!=timedelta(0):raise ValueError('UTC audit clock needed')
 quality=load(root,'data/quality/latest.json')
 if quality is None:raise ValueError('quality audit missing')
 if quality.get('critical_failures') or quality.get('status') not in ('ok_with_coverage_limits','ok_with_source_warnings','ok'):
  raise ValueError('critical data quality failure')
 qtime=utc(quality['audited_at_utc'])
 if qtime>now+timedelta(minutes=5):raise ValueError('quality audit from future')
 sec_age=round((now-qtime).total_seconds()/3600,2)
 # Keep the prior audit's limits visible; do not declare a stale audit "current".
 stages={}
 stages['1_onchain_whales_exchange']={
  'sources':{
   'onchain':view_source(root,'data/onchain/metrics_latest.json','collected_at_utc',now,36),
   'confirmed_whales':view_source(root,'data/whales_blocks/latest.json','ended_at_utc',now,2),
   'utxo_candidate':view_source(root,'data/stage1/rolling_por_utxo_latest.json','generated_at_utc',now,36,('ok_sample_complete',)),
   'schedule_evidence':view_source(root,'data/stage1/schedule_evidence.json','generated_at_utc',now,10,('all_evidence_matched',))},
  'predictor_policy':'research_context_only; whale TX does not prove economic entity activity or exchange netflows',
  'unverified_features':['exchange_netflow','current_PoR_address_ownership','entity_adjusted_whale_flow']}
 onchain=load(root,'data/onchain/metrics_latest.json')
 whales=load(root,'data/whales_blocks/latest.json')
 chain=load(root,'data/stage1/schedule_evidence.json')
 stages['1_onchain_whales_exchange']['observations']={
  'active_addresses_last_complete_day':int(float(onchain['daily_activity_latest']['active_addresses'])) if onchain and onchain.get('daily_activity_latest',{}).get('active_addresses') is not None else None,
  'whale_scanner_confirmed_candidate_events':int(whales['cumulative_unique_whale_transactions']) if whales and whales.get('cumulative_unique_whale_transactions') is not None else None,
  'utxo_scheduled_chain_verified':bool(chain and chain.get('stage1_rolling_utxo_chain_verified') is True),
  'verified_exchange_netflow':False}
 stages['2_liquidations_liquidity']={
  'sources':{
   'sampled_liquidations':view_source(root,'data/liquidations/latest.json','ended_at_utc',now,3,('ok','partial')),
   'liquidity_snapshot':view_source(root,'data/liquidity/latest.json','collected_at_utc',now,3)},
  'predictor_policy':'liquidity snapshots are timestamped, not historical full-market liquidation totals',
  'unverified_features':['global_liquidation_total','full_24h_forced_liquidation_imbalance']}
 liq=load(root,'data/liquidity/latest.json')
 liquidations=load(root,'data/liquidations/latest.json')
 if liq and liq.get('status')=='ok':
  bids=float(liq['orderbook']['bid_depth_usdt_50bps'])
  asks=float(liq['orderbook']['ask_depth_usdt_50bps'])
  if bids<0 or asks<0 or bids+asks==0:raise ValueError('invalid liquidity depth')
  imbalance=(bids-asks)/(bids+asks)
 else:imbalance=None
 stages['2_liquidations_liquidity']['observations']={
  'bid_ask_50bps_imbalance':round(imbalance,4) if imbalance is not None else None,
  'observed_liquidation_listen_window_minutes':round(float(liquidations['listen_seconds'])/60,2) if liquidations else None,
  'liquidation_full_market_coverage':False}
 stages['3_etf']={
  'sources':{'etf_report':view_source(root,'data/etf/latest.json','ended_at_utc',now,96)},
  'observations':read_latest_etfs(root,now),
  'predictor_policy':'snapshot available from first_seen_utc; no historical leakage',
  'unverified_features':['complete_publication_vintage_history','same_day_ex_ante_ETF_flow']}
 stages['4_macro_policy']={
  'sources':{
   'macro':view_source(root,'data/macro/latest.json','ended_at_utc',now,48),
   'fed_bls_calendar':view_source(root,'data/policy_events/latest.json','ended_at_utc',now,48)},
  'observations':{
   'macro_series_count':len((load(root,'data/macro/latest.json') or {}).get('series',{})),
   'policy_calendar_records':(load(root,'data/policy_events/latest.json') or {}).get('records')},
  'predictor_policy':'observation date != release time; calendar schedule != realized CPI or PPI; no inferred DOGE reaction',
  'unverified_features':['historical_FRED_vintages','confirmed_macro_release_surprises','policy_text_directional_effect']}
 stage5=load(root,'data/stage5/latest.json')
 err=load(root,'data/stage5/error_review/latest.json')
 stages['5_history_models']={
  'sources':{
   'history':view_source(root,'data/history/latest.json','ended_at_utc',now,36),
   'forward_audit':view_source(root,'data/stage5/latest.json','generated_at_utc',now,36),
   'error_review':view_source(root,'data/stage5/error_review/latest.json','generated_at_utc',now,36)},
  'observations':{
   '70pct_validation':bool(stage5 and stage5.get('prospective_70pct_validated') is True),
   'prediction_edge_proven':bool(stage5 and stage5.get('directional_prediction_edge_proven') is True),
   'historical_error_analysis_available':bool(err and err.get('status')=='ok')},
  'predictor_policy':'honor frozen V4.2 and future claim gate; historical spot simulation separately labeled',
  'unverified_features':['prospective_70pct_accuracy','prospective_positive_spot_net_edge']}
 base_history=load(root,'data/history/latest.json')
 return {'status':'ok','quality_audit_age_hours':sec_age,
  'quality_audit_stale_for_3h_realtime_claim':sec_age>3,
  'quality_critical_failures':len(quality['critical_failures']),
  'pillars':stages,'pillar_count':len(stages),
  'original_36_items':inspect_legacy36(root),
  'model_input_authorization':{
   'historic_PIT_safe': ['confirmed_DOGE_BTC_daily_prices_with_conservative_one_day_lag'],
   'live_context_not_historically_trainable_without_first_seen':[
     'confirmed_whales','liquidity_depth','limited_liquidation_notices',
     'ETF_flows','FRED_macro','policy_headlines','onchain_address_activity'],
   'unsupported': ['entity_verified_exchange_netflow','whole_market_liquidations',
      'historical_revised_CPI_as_released','news_social_realtime_sentiment'],
   'all_5_pillars_merged_into_single_numeric_forecast':False,
   'claim_36_features_trained':False,
   'safeguard':'Never use post-target or subsequently ingested data as pre-forecast feature'},
  'reconciliation_note':'Five pillars are integrated as source and evidence inventory, NOT fitted causal coefficients.',
  'source_daily_candles_last_day':base_history.get('quality',{}).get('symbols',{}).get('DOGE-USDT',{}).get('last_utc') if base_history else None}
