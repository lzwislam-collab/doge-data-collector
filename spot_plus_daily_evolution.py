#!/usr/bin/env python3
"""Daily evidence ledger for DOGE Spot Plus; research only.

Daily evolution means better MEASUREMENT, not guaranteed improving accuracy.
Uses frozen V4.2 *nonoverlap prospective* stats; never blends pre-freeze,
overlapping forecasts, after-the-fact minute labels or historical trading PnL
into a claim of 70% accuracy / positive real-money strategy edge.
"""
import csv,hashlib,io,json,math,os
from datetime import datetime,timedelta,timezone
from pathlib import Path

from spot_plus_research import inspect_p2_protocol

ROOT=Path(__file__).resolve().parent
BASE=ROOT/'data/stage5/spot_plus'
LATEST=BASE/'evolution_latest.json'
LEDGER=BASE/'evolution_log.csv'
REPORT=ROOT/'reports/DOGE_SPOT_PLUS_DAILY.md'
COLS=['date_utc','created_at_utc','last_daily_candle_utc','quality_age_h',
 'data_state','prospective_24h_n','prospective_24h_scored','prospective_24h_accuracy_pct',
 'prospective_24h_wilson95_lower_pct','prospective_24h_quote_coverage_pct',
 'buy_hold_research_return_pct','best_candidate_research_return_pct',
 'market_edge_validated','70pct_validated','research_priority',
 'plus_source_sha256','stage5_source_sha256','previous_digest','row_digest']

def utc(v):
 t=datetime.fromisoformat(str(v).replace('Z','+00:00'))
 if t.tzinfo is None or t.utcoffset()!=timedelta(0):raise ValueError('UTC required')
 return t.astimezone(timezone.utc)

def read_json(path):
 d=json.loads(path.read_text(encoding='utf-8'))
 if not isinstance(d,dict) or d.get('status')!='ok':
  raise ValueError('invalid source status: '+path.as_posix())
 return d

def sha(path):
 return hashlib.sha256(path.read_bytes()).hexdigest()

def fingerprint(row):
 return hashlib.sha256(json.dumps({k:row[k] for k in COLS if k!='row_digest'},
  ensure_ascii=True,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def read_ledger(path):
 if not path.exists():return []
 with path.open(newline='',encoding='utf-8-sig') as f:
  reader=csv.DictReader(f)
  if reader.fieldnames!=COLS:raise ValueError('daily ledger column mutation')
  rows=list(reader)
 last=None;seen=set();prev='GENESIS'
 for row in rows:
  date=row['date_utc']
  datetime.strptime(date,'%Y-%m-%d')
  if date in seen or (last is not None and date<=last):
   raise ValueError('duplicate or non-monotone daily ledger date')
  if row['previous_digest']!=prev or row['row_digest']!=fingerprint(row):
   raise ValueError('daily ledger integrity hash chain mismatch')
  seen.add(date);last=date;prev=row['row_digest']
 return rows

def serial(rows):
 s=io.StringIO();w=csv.DictWriter(s,fieldnames=COLS,lineterminator='\n')
 w.writeheader();w.writerows(rows)
 return s.getvalue()

def write_atomic(path,text):
 path.parent.mkdir(parents=True,exist_ok=True)
 tmp=path.with_name(path.name+'.tmp')
 tmp.write_text(text,encoding='utf-8')
 os.replace(tmp,path)

def day_stats(stage):
 trial=stage.get('preregistered_forward_trial',{})
 if not trial.get('protocol_frozen') or not trial.get('no_prefreeze_predictions_counted'):
  raise ValueError('not a frozen pre-registered forward trial')
 day=trial.get('horizons',{}).get('24h')
 if not isinstance(day,dict):raise ValueError('24h prospective score missing')
 n=int(day['locked_bins_with_issued_predictions'])
 scored=int(day['scored']);resolved=int(day['resolved']);correct=int(day['primary_correct'])
 if n<0 or not 0<=scored<=resolved<=n or not 0<=correct<=scored:
  raise ValueError('corrupt prospective 24h score counters')
 if stage.get('prospective_70pct_validated') is True:
  # Authorizing trading would require a separate profitable forward economic trial.
  raise ValueError('unexpected global validation claim requires separate release audit')
 return day,n,scored,correct

def make_priority(plus,trial_day,quality_stale,source_warnings,original_quote_quality=None):
 if quality_stale or source_warnings:
  return 'P0_VERIFY_TIMESTAMP_FRESHNESS_AND_MISSING_SOURCE'
 # Fix real-time original quote observability BEFORE collecting 100 samples.
 # A 90% required coverage cannot be reached by selecting only scored rows.
 original_quote_quality=original_quote_quality or {}
 if original_quote_quality.get('matured') is not None and original_quote_quality['matured']>=5:
  if original_quote_quality['quote_coverage_pct'] is not None and original_quote_quality['quote_coverage_pct']<90:
   return 'P0_REPAIR_ORIGINAL_TARGET_TICKER_QUOTE_COVERAGE'
 if trial_day['locked_bins_with_issued_predictions']<100:
  return 'P0_GATHER_LOCKED_NONOVERLAP_24H_FORWARD_SCORES'
 if trial_day.get('coverage_pct') is None or float(trial_day['coverage_pct'])<90:
  return 'P0_REPAIR_PROSPECTIVE_TARGET_QUOTE_COVERAGE'
 history=plus['historical_spot_paper']
 hold=float(history['buy_and_hold']['net_total_return_pct'])
 if all(float(v['net_total_return_pct'])<=hold for k,v in history.items() if k not in ('buy_and_hold','cash')):
  return 'P1_RESEARCH_NEW_PIT_SAFE_MODEL_ON_DISTINCT_FORWARD_COHORT'
 return 'P1_CHECK_REAL_FORWARD_NET_PROFIT_VS_BUYHOLD_AND_CASH'

def checked_market_spot_snapshot(root,now):
 path=root/'data/stage5/spot_plus/live_quote_latest.json'
 if not path.exists():
  return {'status':'missing','live_snapshot_verified':False,
    'reason':'no actual independent OKX spot quote collected for this report'}
 j=read_json(path)
 if (j.get('snapshot_only_not_continuous_market_stream') is not True
     or j.get('not_an_executable_fill') is not True
     or j.get('change_to_frozen_v42_or_dca') is not False):
  raise ValueError('live quote provenance unsupported')
 observed=utc(j['observed_at_utc'])
 age=(now-observed).total_seconds()
 if age<0 or age>180:
  return {'status':'stale','live_snapshot_verified':False,
    'observed_at_utc':observed.isoformat(),'age_seconds':round(age,2)}
 q=j.get('instruments',{})
 if set(q)!=set(('DOGE-USDT','BTC-USDT','ETH-USDT')):
  raise ValueError('incomplete live spot quote symbols')
 out={}
 for symbol in ('DOGE-USDT','BTC-USDT','ETH-USDT'):
  d=q[symbol]
  ticker_at=utc(d['source_quote_at_utc'])
  current_lag=(now-ticker_at).total_seconds()
  bid=float(d['best_bid_usdt']);ask=float(d['best_ask_usdt'])
  price=float(d['last_usdt'])
  if (d.get('inst_id')!=symbol or not math.isfinite(price)
      or not math.isfinite(bid) or not math.isfinite(ask)
      or bid<=0 or ask<bid or price<=0 or current_lag<0 or current_lag>180):
   raise ValueError('invalid live ticker or stale quote '+symbol)
  out[symbol]={'price_usdt':price,'quote_at_utc':ticker_at.isoformat(),
    'bid_usdt':bid,'ask_usdt':ask,'quote_age_seconds':round(current_lag,2)}
 return {'status':'ok','live_snapshot_verified':True,
  'observed_at_utc':observed.isoformat(),'age_seconds':round(age,2),
  'instruments':out,
  'this_is_not_a_continuous_stream_or_executable_trade':True}


def inspect(root=ROOT,now=None):
 if now is None:now=datetime.now(timezone.utc)
 if now.tzinfo is None or now.utcoffset()!=timedelta(0):raise ValueError('UTC audit time')
 pp=root/'data/stage5/spot_plus/latest.json'
 sp=root/'data/stage5/latest.json'
 plus=read_json(pp);stage=read_json(sp)
 # Do not present yesterday's successful report as a live market quote.
 pt=utc(plus['generated_at_utc']);st=utc(stage['generated_at_utc'])
 if pt>now+timedelta(minutes=2) or st>now+timedelta(minutes=2):
  raise ValueError('source report from future')
 if abs((pt-st).total_seconds())>3600:raise ValueError('Plus and Stage5 audit cutoffs too far apart')
 context=plus['five_pillar_evidence_audit']
 if context.get('pillar_count')!=5 or len(context.get('pillars',{}))!=5:
  raise ValueError('missing five-pillar audit')
 if (plus.get('profit_edge_vs_hold_verified') is not False
     or plus.get('direction_accuracy_70pct_verified') is not False
     or plus.get('live_trading_authorized') is not False
     or plus.get('dca_rules_changed') is not False
     or stage.get('claim_permission')!='NO_VALIDATED_EDGE'):
  raise ValueError('unsupported model/trading authorization')
 ts,registered,scored,correct=day_stats(stage)
 if ts.get('primary_accuracy_pct') is not None and round(100*correct/scored,2)!=float(ts['primary_accuracy_pct']):
  raise ValueError('24h primary accuracy not supported by count')
 quality_age=float(context['quality_audit_age_hours'])
 qual_source=utc(plus['generated_at_utc'])
 quality_origin=(utc(context['quality_source_as_of_utc']) if context.get('quality_source_as_of_utc')
                 else qual_source-timedelta(hours=quality_age))
 effective_age=(now-quality_origin).total_seconds()/3600
 stale=(effective_age>3)
 source_warnings=[]
 if stale:source_warnings.append('GLOBAL_QUALITY_AUDIT:older_than_3h')
 for name,pillar in context['pillars'].items():
  for src,entry in pillar.get('sources',{}).items():
   reported=entry.get('state')
   if reported in ('missing','unhealthy','stale','backlog'):
    source_warnings.append(f'{name}/{src}:{reported}')
    continue
   stamp=entry.get('source_utc')
   if stamp:
    from_now=(now-utc(stamp)).total_seconds()/3600
    if from_now < -0.1 or from_now>48:
     source_warnings.append(f'{name}/{src}:outdated_or_future')
 daily_last=utc(plus['source_daily_last_available_at_utc'])
 if daily_last>now+timedelta(minutes=2):
  raise ValueError('daily source candle from future')
 if now-daily_last>timedelta(hours=30):
  source_warnings.append('DOGE_1D:older_than_30h')
 simulations=plus['historical_spot_paper']
 ref=float(simulations['buy_and_hold']['net_total_return_pct'])
 candidates=[(k,float(v['net_total_return_pct'])) for k,v in simulations.items()
  if k not in ('buy_and_hold','cash')]
 if not candidates:raise ValueError('no cash vs buy/hold comparison')
 best=max(candidates,key=lambda kv:kv[1])
 if any(not math.isfinite(v) for k,v in candidates+[('buy_and_hold',ref)]):
  raise ValueError('invalid hypothetical profitability')
 # Prefreeze quote diagnostic is allowed to identify COLLECTION failures,
 # but is never incorporated into post-freeze directional accuracy.
 forward_live=stage.get('forward_live',{}).get('horizons',{})
 sample=forward_live.get('1h',{})
 try:
  matured=int(sample.get('resolved',0))
  scores=int(sample.get('scored',0))
  uncovered=int(sample.get('unscorable',0))
 except (TypeError,ValueError):raise ValueError('invalid original ticker coverage counters')
 if matured<0 or scores<0 or uncovered<0 or scores+uncovered!=matured:
  raise ValueError('original ticker coverage accounting mismatch')
 rate=round(100*scores/matured,2) if matured else None
 if rate is not None and sample.get('resolved_quote_coverage_pct') is not None and abs(rate-float(sample['resolved_quote_coverage_pct']))>0.011:
  raise ValueError('original ticker quote coverage mismatch')
 ticker_quality={'matured':matured,'scored':scores,'unscorable':uncovered,
  'quote_coverage_pct':rate,'required_quote_coverage_pct':90,
  'coverage_priority_uses_prefreeze_collection_only_not_directional_accuracy':True}
 priority=make_priority(plus,ts,stale,source_warnings,ticker_quality)
 market_snapshot=checked_market_spot_snapshot(root,now)
 official=inspect_official_v2(root,now)
 if official['status']=='ok':
  if official['is_stale_over_3h']:
   source_warnings.append('V2_OFFICIAL_REPORT:older_than_3h')
  issued=official['1h']
  if issued['expected_issuances']>=3 and issued['issuance_coverage_pct'] is not None and issued['issuance_coverage_pct']<90:
   priority='P0_REPAIR_OFFICIAL_V2_PREDICTION_ISSUANCE_COVERAGE'
  elif official['24h']['nonoverlap_due']>=5 and official['24h']['nonoverlap_quote_coverage_pct'] is not None and official['24h']['nonoverlap_quote_coverage_pct']<90:
   priority='P0_REPAIR_OFFICIAL_V2_TARGET_QUOTE_COVERAGE'
 if official['status']=='missing' and now>=datetime(2026,10,12,3,0,tzinfo=timezone.utc):
  source_warnings.append('V2_OFFICIAL_REPORT:missing')
 return {'schema_version':1,'status':'ok','generated_at_utc':now.isoformat(),
  'reporting_as_of_utc':now.isoformat(),
  'p2_economic_protocol':inspect_p2_protocol(root,now),
  'source_freshness':{name:{key:dict(entry,reporting_as_of_utc=now.isoformat(),
      age_hours=round((now-utc(entry['source_utc'])).total_seconds()/3600,2) if entry.get('source_utc') else None,
      freshness=('future' if now<utc(entry['source_utc']) else
        'stale' if (now-utc(entry['source_utc'])).total_seconds()/3600>entry.get('freshness_limit_hours',48) else 'current')
       if entry.get('source_utc') else 'missing')
    for key,entry in pillar.get('sources',{}).items()}
    for name,pillar in context['pillars'].items()},
  'ledger_date_utc':now.date().isoformat(),
  'underlying_sources':{'spot_plus_generated_at_utc':pt.isoformat(),
   'stage5_generated_at_utc':st.isoformat(),
   'last_daily_source_complete_at_utc':daily_last.isoformat(),
   'spot_plus_sha256':sha(pp),'stage5_sha256':sha(sp)},
  'source_health':{'grade':'WARN' if stale or source_warnings else 'PARTIAL_COVERAGE',
   'freshness_gate':'WARN' if stale or source_warnings else 'PASS',
   'live_market_tick_verified':market_snapshot['live_snapshot_verified'],
   'data_quality_pass_does_not_imply_full_coverage':True,
   'global_quality_audit_age_hours_now':round(effective_age,2),
   'global_quality_audit_stale_over_3h':stale,
   'warning_sources':source_warnings,
   'snapshot_data_not_exchange_tick_realtime':True},
  'current_spot_quote':market_snapshot,
  'official_V2_forward_validation':official,
  'original_36_status':context['original_36_items'],
  'all_five_pillars_in_inventory':True,
  'all_five_pillars_in_prediction_weights':False,
  'original_ticker_1h_collection_diagnostic':ticker_quality,
  'prospective_24h':{'locked_bin_registered':registered,
   'genuinely_scored':scored,'correct':correct,
   'accuracy_pct':ts.get('primary_accuracy_pct'),
   'wilson95_lower_pct':ts.get('wilson95_lower_pct_descriptive_only'),
   'coverage_pct':ts.get('coverage_pct'),
   'baseline_accuracy_pct':ts.get('baseline_majority_accuracy_pct'),
   'up_recall_pct':ts.get('up_recall_pct'),
   'down_recall_pct':ts.get('down_recall_pct'),
   'eligible_for_70pct_claim':ts.get('eligible_for_70pct_claim') is True},
  'historical_paper':{'nonblind_selection':True,'buy_hold_pct':ref,
   'cash_pct':float(simulations['cash']['net_total_return_pct']),
   'best_candidate_id':best[0],'best_candidate_pct':best[1],
   'candidate_outperformance_vs_hold_pct_points':round(best[1]-ref,2),
   'candidate_selection_not_forward_validation':True,
   'fee_and_slippage_assumptions_applied':True},
  'evolution_priority':priority,
  'evolution_process':['inspect timestamp/coverage and keep missing visible',
    'verify matured frozen quote labels and independent minute labels separately',
    'classify wrong vs missing plus contemporaneous BTC/ETH association',
    'draft no-more-than-one new PIT-auditable candidate at a time',
    'do chronological purged and embargoed tests without tuning on untouched holdout',
    'pre-register challenger before prospective paper trading, no silent incumbent change',
    'compare net equity after fees/slippage with DOGE buy-hold AND cash plus drawdown',
    'only after independent sign-off propose promotion, never automatic DCA modification'],
  'economic_edge_validated':False,'accuracy_70pct_validated':False,
  'integrated_prediction_readiness':'BLOCKED_UNTIL_PIT_AND_FORWARD_PROOF',
  'stage1_exch_netflow_not_verified':True,
  'stage2_full_liquidation_coverage_not_verified':True,
  'ETF_MACRO_point_in_time_backtest_not_verified':True,
  'original_36_item_level_reconciliation_complete':bool(context['original_36_items'].get('item_level_current_mapping_reconciled')),
  'model_change_authorized':False,'real_spot_orders_authorized':False,
  'daily_code_or_model_improvement_proven':False,
  'note':'A daily new audit is not evidence the signal has improved; 70% cannot be forced.'}


def inspect_official_v2(root,now):
 """V2 is the ONLY official blind-forward evidence; older V1 is diagnostic."""
 candidates=[root/'data/stage5/forward_validation_v2'/name for name in ('latest.json','daily_latest.json')]
 present=[(utc(read_json(path)['audited_at_utc']),path) for path in candidates if path.exists()]
 file=max(present,key=lambda item:item[0])[1] if present else candidates[0]
 if not file.exists():
  return {'status':'missing','is_official':True,'available':False,
   'accuracy_70pct_validated':False,'economic_edge_validated':False,
   'reason':'No separately frozen V2 evidence has been persisted yet'}
 v=read_json(file)
 stamp=utc(v['audited_at_utc'])
 freeze=utc(v['effective_from_utc'])
 if stamp>now+timedelta(minutes=2):
  raise ValueError('official V2 report timestamp is in future')
 if v.get('protocol_id')!='DOGE_FORWARD_RAW_QUOTE_BOUND_V2_20261012_0200':
  raise ValueError('unexpected V2 protocol identity')
 if v.get('locked_implementation_checked') is not True:
  raise ValueError('V2 implementation lock not checked')
 if v.get('accuracy_70pct_validated') is not False or v.get('economic_edge_validated') is not False:
  raise ValueError('unsupported V2 performance claim')
 def hstat(h):
  data=v['horizons'][h]
  chosen=data['nonoverlap']
  issue=data['issuance']
  expected=int(issue['expected']);issued=int(issue['issued'])
  due=int(chosen['due']);scored=int(chosen['scored']);unresolved=int(chosen['missing_or_unresolved'])
  if min(expected,issued,due,scored,unresolved)<0 or issued>expected or scored+unresolved!=due:
   raise ValueError('V2 issuance/scoring accounting mismatch '+h)
  rate=(100*issued/expected) if expected else None
  if rate is not None and abs(rate-float(issue['coverage_pct']))>0.1:
   raise ValueError('V2 issuance coverage inconsistent '+h)
  correct=int(chosen['models']['doge_reversal']['correct'])
  if correct<0 or correct>scored:raise ValueError('V2 correct-count corruption')
  return {'expected_issuances':expected,'issued':issued,'issuance_coverage_pct':round(rate,2) if rate is not None else None,
   'nonoverlap_due':due,'nonoverlap_scored':scored,'nonoverlap_missing':unresolved,
   'nonoverlap_accuracy_pct':chosen['models']['doge_reversal']['accuracy_pct'],
   'nonoverlap_wilson95_lower_pct':chosen['models']['doge_reversal']['wilson95_lower_pct_descriptive_only'],
   'nonoverlap_quote_coverage_pct':chosen['quote_coverage_pct'],
   'edge_validated':chosen['edge_validated'] is True}
 economic=v['prospective_spot_paper']
 return {'status':'ok','is_official':True,'available':True,
  'source_file':file.relative_to(root).as_posix(),
  'source_as_of_utc':stamp.isoformat(),'reporting_as_of_utc':now.isoformat(),
  'freshness':'stale' if now-stamp>timedelta(hours=3) else 'current',
  'generated_at_utc':stamp.isoformat(),'age_hours':round((now-stamp).total_seconds()/3600,2),
  'is_stale_over_3h':now-stamp>timedelta(hours=3),
  'effective_from_utc':freeze.isoformat(),
  '1h':hstat('1h'),'24h':hstat('24h'),
  'raw_quote_snapshots':int(v['raw_quote_snapshots']),
  'quote_bindings':int(v['quote_bindings']),
  'completed_paper_intervals':int(economic['completed_intervals']),
  'paper_net_return_pct':economic['strategy_net_return_pct'],
  'paper_buy_hold_return_pct':economic['buy_hold_net_return_pct'],
  'accuracy_70pct_validated':False,'economic_edge_validated':False,
  'V1_statistics_are_not_V2_claim_evidence':True}

def log_row(summary):
 p=summary['prospective_24h'];h=summary['historical_paper']
 raw={'date_utc':summary['ledger_date_utc'],
  'created_at_utc':summary['generated_at_utc'],
  'last_daily_candle_utc':summary['underlying_sources']['last_daily_source_complete_at_utc'],
  'quality_age_h':str(summary['source_health']['global_quality_audit_age_hours_now']),
  'data_state':summary['source_health']['grade'],
  'prospective_24h_n':str(p['locked_bin_registered']),
  'prospective_24h_scored':str(p['genuinely_scored']),
  'prospective_24h_accuracy_pct':str(p['accuracy_pct']) if p['accuracy_pct'] is not None else '',
  'prospective_24h_wilson95_lower_pct':str(p['wilson95_lower_pct']) if p['wilson95_lower_pct'] is not None else '',
  'prospective_24h_quote_coverage_pct':str(p['coverage_pct']) if p['coverage_pct'] is not None else '',
  'buy_hold_research_return_pct':str(h['buy_hold_pct']),
  'best_candidate_research_return_pct':str(h['best_candidate_pct']),
  'market_edge_validated':'false','70pct_validated':'false',
  'research_priority':summary['evolution_priority'],
  'plus_source_sha256':summary['underlying_sources']['spot_plus_sha256'],
  'stage5_source_sha256':summary['underlying_sources']['stage5_sha256'],
  'previous_digest':'GENESIS','row_digest':''}
 return raw

def render(s,record):
 p=s['prospective_24h'];h=s['historical_paper']
 def val(v):return '尚无有效样本' if v is None else str(v)+'%'
 warnings=', '.join(s['source_health']['warning_sources']) or '无'
 spot=s['current_spot_quote']
 quote=spot.get('instruments',{}).get('DOGE-USDT',{})
 quote_desc=(f"OKX DOGE/USDT {quote['price_usdt']}；报价UTC {quote['quote_at_utc']}；"
  "独立快照，不是连续行情或已验证交易成交价" if spot['live_snapshot_verified']
  else f"未验证（{spot['status']}），不可冒充实时价格")
 official=s['official_V2_forward_validation']
 if official['status']=='ok':
  one=official['1h'];h24=official['24h']
  v2intro=(f"## 正式验收：V2 冻结前瞻（唯一有效正式成绩）\n\n"
    f"- V2 报告UTC：{official['generated_at_utc']}，报价快照数：{official['raw_quote_snapshots']}。\n"
    f"- 1小时发行：{one['issued']}/{one['expected_issuances']}，覆盖率 {val(one['issuance_coverage_pct'])}。\n"
    f"- 24小时非重叠到期：{h24['nonoverlap_due']}，有效评分 {h24['nonoverlap_scored']}，漏评 {h24['nonoverlap_missing']}；"
    f"准确率 {val(h24['nonoverlap_accuracy_pct'])}，Wilson95下限 {val(h24['nonoverlap_wilson95_lower_pct'])}。\n"
    f"- 前瞻现货纸面完成区间：{official['completed_paper_intervals']}；"
    f"净收益 {val(official['paper_net_return_pct'])}。70%与盈利优势均未证明。\n\n")
 else:
  v2intro="## 正式验收：V2 不可用\n\n缺少正式 V2 成绩，不能采用旧协议替代。\n\n"
 p2=s.get('p2_economic_protocol',{})
 source_lines=''.join(f"- {name}/{key}：来源截至 {entry.get('source_utc','缺失')}；状态 {entry.get('freshness',entry.get('state','缺失'))}。\n"
  for name,pillar in s.get('source_freshness',{}).items() for key,entry in pillar.items())
 provenance=(f"## 统一报告时点与底层来源\n\n本次报告截至 {s.get('reporting_as_of_utc',s['generated_at_utc'])}。报告生成时间不代表底层来源同步更新。\n\n"
  +source_lines+f"\nP2经济协议：{p2.get('state','missing')}；生效时间 {p2.get('effective_from_utc','未验证')}；读回来源截至 {p2.get('source_as_of_utc','缺失')}。现货盈利优势仍未验证。\n"
  +"V4.2参考沿用冻结发行注册表，不是Plus新发行预测；没有经校准胜率。实体净流入、全市场清算和历史PIT宏观仍未获模型输入授权。\n\n")
 return ('# DOGE 现货预测 Plus｜每日进化审计\n\n'
  '实施顺序以 [最新权威路线图](DOGE_NEXT_STAGE_IMPLEMENTATION_2026-10-12.md) 为准：P0优先，P1/P2同步且不阻塞积累；工程PASS不等于运行、预测或盈利达标。冻结协议不变。\n\n'
  '新版独立补充证据见 [Plus证据层V1.0](DOGE_PLUS_EVIDENCE_V1_0.md)；本报告仍属于旧Plus研究轨，历史收益和正式V2成绩不合并进新版。\n\n'
  f"**现货即时快照：** {quote_desc}  \n"
  f"**运行UTC：** {s['generated_at_utc']}  \n"
  f"**本次数据状态：** {s['source_health']['grade']}，来源警告：{warnings}  \n"
  '**注意：** 时效性 PASS 不等于数据覆盖全面，更不代表预测有效或现货盈利。五大模块均作为背景信息，尚未形成已验证的统一量化预测信号。  \n'
  f"**最新确认日线数据可用时点：** {s['underlying_sources']['last_daily_source_complete_at_utc']}  \n"
  f"**本日账本：** {'新建记录' if record else '保留本日首次记录，不重复写入'}\n\n"
  + provenance
  + v2intro
  + '## ① 旧 V4.2/V1 辅助诊断（不计入 V2 正式成绩）\n\n'
  f"- 已发行可独立分箱预测：{p['locked_bin_registered']}；取得有效评分：{p['genuinely_scored']}；正确：{p['correct']}。\n"
  f"- 方向准确率：{val(p['accuracy_pct'])}；95% Wilson下限：{val(p['wilson95_lower_pct'])}；报价覆盖率：{val(p['coverage_pct'])}。\n"
  '- 70%有效优势：**未验证**。历史和冻结前诊断不计入正式成绩。\n'
  f"- **原始行情采集健康（诊断，非预测成绩）：** 1小时到期 {s['original_ticker_1h_collection_diagnostic']['matured']} 条，有效 {s['original_ticker_1h_collection_diagnostic']['scored']} 条，漏评 {s['original_ticker_1h_collection_diagnostic']['unscorable']} 条，报价覆盖率 {val(s['original_ticker_1h_collection_diagnostic']['quote_coverage_pct'])}，最低要求90%。\n\n"
  '## ② 现货经济能力（仅历史假设模拟）\n\n'
  f"- DOGE买入持有：{h['buy_hold_pct']}%；现金：{h['cash_pct']}%；历史最好候选 {h['best_candidate_id']}：{h['best_candidate_pct']}%。\n"
  f"- 与持币比较：{h['candidate_outperformance_vs_hold_pct_points']}个百分点；候选通过历史观察筛选，**不构成未来获利证据**。\n\n"
  '## ③ 与前一日相比\n\n'
  f"- {s['change_since_previous_daily_record']['summary']}\n\n"
  '## ④ 今日最优先整改\n\n'
  f"**{s['evolution_priority']}**\n\n"
  '逐项保留缺失与时点限制，次日复查是否修复。模型升级须另外独立验证。'
  '不自动交易、不更改DOGE定投、不保证70%。\n')

def run(root=ROOT,now=None):
 summary=inspect(root,now)
 path=root/'data/stage5/spot_plus/evolution_log.csv'
 old=read_ledger(path)
 date=summary['ledger_date_utc']
 append=(not old or date>old[-1]['date_utc'])
 if old and date<old[-1]['date_utc']:
  raise ValueError('attempt to backdate evolution ledger')
 if append:
  row=log_row(summary)
  row['previous_digest']=old[-1]['row_digest'] if old else 'GENESIS'
  row['row_digest']=fingerprint(row)
 # Existing day's first immutable row is not overwritten by later push runs.
 previous=(old[-1] if append and old else old[-2] if len(old)>1 else None)
 if previous is None:
  change={'summary':'首日基线，无前日对比数据','previous_date_utc':None,
    'new_24h_predictions':None,'new_24h_scored':None,'new_24h_correct':None}
 else:
  p=summary['prospective_24h']
  new_n=p['locked_bin_registered']-int(previous['prospective_24h_n'])
  new_s=p['genuinely_scored']-int(previous['prospective_24h_scored'])
  # Previous correct count is not stored in v1 ledger; record scored delta
  # and refuse to invent a "correct count improved" history.
  if new_n<0 or new_s<0:raise ValueError('prospective evidence counters regressed')
  change={'summary':f'相对{previous["date_utc"]}：新增正式预测{new_n}次，新增合格评分{new_s}次；这不是模型性能提高的证明',
    'previous_date_utc':previous['date_utc'],
    'new_24h_predictions':new_n,'new_24h_scored':new_s,'new_24h_correct':None}
 summary['change_since_previous_daily_record']=change
 summary['ledger_rows']=len(old)+int(append)
 summary['new_ledger_row']=append
 summary['ledger_last_date_utc']=date if append else old[-1]['date_utc']
 summary['ledger_integrity_checked']=True
 # Compute and validate outputs BEFORE writing the ledger, to avoid partial evidence on failure.
 new_json=json.dumps(summary,ensure_ascii=False,indent=2)+'\n'
 new_markdown='\n'.join(line.rstrip() for line in render(summary,append).splitlines())+'\n'
 new_ledger=serial(old+[row]) if append else None
 if append:write_atomic(path,new_ledger)
 write_atomic(root/'data/stage5/spot_plus/evolution_latest.json',new_json)
 write_atomic(root/'reports/DOGE_SPOT_PLUS_DAILY.md',new_markdown)
 return summary

def main():
 try:
  x=run()
  print(json.dumps({'status':'ok','date':x['ledger_date_utc'],
   'rows':x['ledger_rows'],'new':x['new_ledger_row'],
   'health':x['source_health']['grade'],
   'valid70':x['accuracy_70pct_validated']}))
  return 0
 except Exception as e:
  print(json.dumps({'status':'blocked','error':type(e).__name__+': '+str(e)[:240]}))
  return 1

if __name__=='__main__':
 raise SystemExit(main())
