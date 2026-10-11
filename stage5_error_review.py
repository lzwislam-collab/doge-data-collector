#!/usr/bin/env python3
"""DOGE Stage 5 automatic forecast score/postmortem review (read-only).

Separate, immutable original ticker and minute-close tracks.
Observational market co-movement is NEVER evidence of a single causal driver.
No frozen model, score, registry, DCA rule or prospective protocol is altered.
"""
import csv,json,math,os
from collections import Counter
from datetime import datetime,timedelta,timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parent
OUTPUT=ROOT/'data/stage5/error_review/latest.json'
SOURCE_MODEL='DOGE_DIRECTION_ISSUED_QUOTE_SHADOW_V42_1'
FORECAST_COLS={'forecast_id','model_id','issued_at_utc','source_quote_utc','reference_price',
               'horizon_hours','target_utc','majority_direction','doge_reversal_direction','btc_momentum_direction'}
SCORE_COLS={'forecast_id','status','observed_at_utc','target_quote_utc','target_price',
            'realized_up','majority_correct','doge_reversal_correct','btc_momentum_correct'}
MINUTE_COLS={'forecast_id','status','evaluated_at_utc','target_utc','bar_open_utc','bar_closed_at_utc',
             'retrieved_at_utc','minute_close_usdt','realized_up','majority_correct','doge_reversal_correct',
             'btc_momentum_correct','evaluation_cohort'}

def utc(value):
 d=datetime.fromisoformat(str(value).replace('Z','+00:00'))
 if d.tzinfo is None or d.utcoffset()!=timedelta(0):raise ValueError('UTC timestamp required')
 return d.astimezone(timezone.utc)

def read_csv(root,path,required):
 full=root/path
 with full.open(newline='',encoding='utf-8-sig') as f:
  r=csv.DictReader(f)
  if not required.issubset(r.fieldnames or []):raise ValueError('missing columns '+path)
  records={}
  for v in r:
   key=v.get('forecast_id')
   if not key or key in records:raise ValueError('duplicate/empty forecast_id '+path)
   records[key]=v
  return records

def load_hourly(root):
 """Archive contains confirmed 1H bars; record first_ingested for hindsight caveat."""
 path=root/'data/hourly_research/ohlc.csv'
 if not path.exists():return {}
 with path.open(newline='',encoding='utf-8-sig') as stream:
  reader=csv.DictReader(stream)
  if not {'symbol','open_utc_ms','close','first_ingested_utc'}.issubset(reader.fieldnames or []):
   raise ValueError('hourly archive schema invalid')
  data={x:{} for x in ('BTC-USDT','ETH-USDT','DOGE-USDT')}
  for x in reader:
   sym=x['symbol']
   if sym not in data:continue
   tick=int(x['open_utc_ms']);close=float(x['close'])
   if tick%3_600_000 or close<=0 or not math.isfinite(close):raise ValueError('invalid hourly bar')
   if tick in data[sym]:raise ValueError('duplicate hourly timestamp '+sym)
   first=utc(x['first_ingested_utc'])
   if first<datetime.fromtimestamp((tick+3600000)/1000,timezone.utc)-timedelta(minutes=1):
    raise ValueError('hourly bar first seen before close')
   data[sym][tick]=(close,first)
 return data

def market_context(hourly,issued,target):
 """Retrospective coarse BTC/ETH returns from closes safely preceding endpoints."""
 if not hourly:return {'state':'insufficient_data','explanation':'hourly archive not available'}
 h=3_600_000
 # Last bars complete at issuance/target; never use an in-progress bar.
 a=(int(issued.timestamp()*1000)//h-1)*h
 b=(int(target.timestamp()*1000)//h-1)*h
 outcome={'state':'retrospective_association_only','bar_anchor_ms':a,'bar_target_ms':b,
          'historical_retrieval_is_not_proof_of_real_time_availability':True,
          'returns_pct':{},'source_first_seen_after_issuance':False}
 for sym in ('BTC-USDT','ETH-USDT','DOGE-USDT'):
  pair=hourly.get(sym,{})
  if a not in pair or b not in pair:
   return {'state':'insufficient_data','explanation':'missing already-closed BTC/ETH/DOGE hourly anchors'}
  old,seen1=pair[a];new,seen2=pair[b]
  if seen1>issued or seen2>target:
   outcome['source_first_seen_after_issuance']=True
  outcome['returns_pct'][sym]=round(100*(new/old-1),4)
 return outcome

def flag_checked(forecast,score):
 price=float(score['target_price'])
 ref=float(forecast['reference_price'])
 if not math.isfinite(price) or price<=0 or not math.isfinite(ref) or ref<=0:
  raise ValueError('invalid reference/target price')
 true='UP' if price>ref else 'DOWN'
 if score['realized_up']!=true:raise ValueError('mismatched original quote truth')
 for name in ('majority','doge_reversal','btc_momentum'):
  flag='1' if forecast[name+'_direction']==true else '0'
  if score[name+'_correct']!=flag:raise ValueError('mismatched quote correct flag')
 return true

def minute_checked(forecast,score):
 price=float(score['minute_close_usdt']);ref=float(forecast['reference_price'])
 if not math.isfinite(price) or price<=0 or not math.isfinite(ref) or ref<=0:
  raise ValueError('invalid minute price')
 true='UP' if price>ref else 'DOWN'
 if score['realized_up']!=true:raise ValueError('mismatched minute truth')
 target=utc(forecast['target_utc']);bar_open=utc(score['bar_open_utc'])
 bar_close=utc(score['bar_closed_at_utc']);seen=utc(score['retrieved_at_utc'])
 if (score['target_utc']!=forecast['target_utc']
     or bar_open<target or bar_open>=target+timedelta(minutes=1)
     or bar_close!=bar_open+timedelta(minutes=1)
     or not target<bar_close<=target+timedelta(minutes=2)
     or seen<bar_close or utc(score['evaluated_at_utc'])<bar_close):
  raise ValueError('minute truth has invalid causal time')
 for name in ('majority','doge_reversal','btc_momentum'):
  if score[name+'_correct']!=('1' if forecast[name+'_direction']==true else '0'):
   raise ValueError('mismatched minute flag')
 return true

def causes(true,context):
 """Context indicators; not post-hoc proof of cause or actionable new rules."""
 if context['state']!='retrospective_association_only':
  return ['unattributed_insufficient_hourly_context']
 x=context['returns_pct'];doge=x['DOGE-USDT'];btc=x['BTC-USDT'];eth=x['ETH-USDT']
 labels=[]
 same_sign=(btc<0 and eth<0 and true=='DOWN') or (btc>0 and eth>0 and true=='UP')
 if same_sign and abs(btc)>=0.5 and abs(eth)>=0.5:labels.append('btc_eth_co_movement_correlated')
 if doge-btc<=-1:labels.append('doge_underperformed_btc')
 if doge-btc>=1:labels.append('doge_outperformed_btc')
 if not labels:labels.append('no_dominant_hourly_proxy')
 return labels

def evaluate(root=ROOT,now=None,hourly_override=None):
 if now is None:now=datetime.now(timezone.utc)
 if now.tzinfo is None or now.utcoffset()!=timedelta(0):raise ValueError('audit clock must be UTC')
 lock=json.loads((root/'data/stage5/pre_registration.json').read_text(encoding='utf-8'))
 minute_lock=json.loads((root/'data/stage5/minute_evaluation_protocol.json').read_text(encoding='utf-8'))
 if (lock.get('status')!='frozen_protocol' or minute_lock.get('status')!='frozen_protocol'
     or lock['model_id']!=SOURCE_MODEL or minute_lock['forecast_source_model_id']!=SOURCE_MODEL
     or lock['effective_from_utc']!=minute_lock['effective_from_utc']):
  raise ValueError('frozen studies not aligned')
 freeze=utc(lock['effective_from_utc'])
 reg=read_csv(root,'data/direction_v42/forward_registry.csv',FORECAST_COLS)
 original=read_csv(root,'data/direction_v42/forward_scores.csv',SCORE_COLS)
 minute=read_csv(root,'data/stage5/minute_shadow/minute_scores.csv',MINUTE_COLS)
 if any(fid not in reg for fid in original) or any(fid not in reg for fid in minute):
  raise ValueError('orphan scored forecast')
 hourly=load_hourly(root) if hourly_override is None else hourly_override
 by_track={'original_ticker':[], 'confirmed_1m_close':[]}
 forecast_watch=[]
 for fid,f in sorted(reg.items(),key=lambda q:q[1]['issued_at_utc']):
  if f['model_id']!=SOURCE_MODEL:raise ValueError('unexpected model id')
  issued=utc(f['issued_at_utc']);target=utc(f['target_utc']);h=int(f['horizon_hours'])
  if h not in (1,24,168,720) or target!=issued+timedelta(hours=h):
   raise ValueError('bad registration horizon '+fid)
  if issued>now+timedelta(minutes=5):raise ValueError('future registration')
  cohort='preregistered_forward' if issued>=freeze else 'pre_freeze_diagnostic'
  if h==24 and issued>=now-timedelta(hours=48):
   forecast_watch.append({'forecast_id':fid,'issued_at_utc':issued.isoformat(),
    'target_utc':target.isoformat(),'original_direction':f['doge_reversal_direction'],
    'reference_price':float(f['reference_price']),
    'status':'pending' if target>now else ('score_recorded' if fid in original else 'awaiting_valid_score'),
    'do_not_change_original_forecast':True})
  for name,score in [('original_ticker',original.get(fid)),('confirmed_1m_close',minute.get(fid))]:
   if score is None:
    state='pending' if target>now else 'awaiting_source_score'
    category='not_an_error_yet' if state=='pending' else 'unresolved_coverage'
    if target<=now-timedelta(hours=18):category='overdue_missing_score'
    outcome=None; context=None;labels=[]
   elif name=='original_ticker':
    status=score['status']
    if status=='unscorable_missing_target_quote':
     if any(score.get(k) for k in ('target_quote_utc','target_price','realized_up','doge_reversal_correct')):
      raise ValueError('unscorable quote contains invented outcome')
     if target>utc(score['observed_at_utc']):raise ValueError('unscorable before target')
     state='unscorable';category='observation_coverage_failure';outcome=None;context=None;labels=['target_ticker_not_observed_within_25min']
    elif status=='scored':
     seen=utc(score['target_quote_utc'])
     if not target<=seen<=target+timedelta(minutes=25):raise ValueError('quote not inside score window')
     true=flag_checked(f,score)
     state='scored';category='direction_wrong' if score['doge_reversal_correct']=='0' else 'direction_correct'
     outcome=true
     context=market_context(hourly,issued,target) if category=='direction_wrong' else None
     labels=causes(true,context) if context else []
    else:raise ValueError('unknown source quote score status')
   else:
    if score['status']!='scored_from_confirmed_1m_close':raise ValueError('bad minute status')
    expected='preregistered_forward_shadow' if issued>=freeze else 'pre_freeze_diagnostic_only'
    if score['evaluation_cohort']!=expected or score['protocol_id']!=minute_lock['protocol_id']:
     raise ValueError('wrong minute scoring cohort')
    true=minute_checked(f,score)
    state='scored';category='direction_wrong' if score['doge_reversal_correct']=='0' else 'direction_correct'
    outcome=true
    context=market_context(hourly,issued,target) if category=='direction_wrong' else None
    labels=causes(true,context) if context else []
   event={'forecast_id':fid,'cohort':cohort,'track':name,'horizon_hours':h,
    'issued_at_utc':issued.isoformat(),'target_utc':target.isoformat(),
    'predicted_direction':f['doge_reversal_direction'],'outcome_direction':outcome,
    'status':state,'category':category,'explanatory_factors':labels,
    'context':context,'classification_certainty':'observed_result_only_no_causal_attribution',
    'model_revision_authorized':False}
   by_track[name].append(event)
 cohorts={}
 for track,events in by_track.items():
  for h in (1,24,168,720):
   for cohort in ('pre_freeze_diagnostic','preregistered_forward'):
    group=[e for e in events if e['horizon_hours']==h and e['cohort']==cohort]
    scored=[e for e in group if e['status']=='scored']
    wrong=[e for e in scored if e['category']=='direction_wrong']
    n=len(scored)
    by_direction={d:{'scored':len([e for e in scored if e['predicted_direction']==d]),
      'wrong':len([e for e in wrong if e['predicted_direction']==d])} for d in ('UP','DOWN')}
    by_factor=dict(Counter(l for e in wrong for l in e['explanatory_factors']))
    cohorts[track+'_'+str(h)+'h_'+cohort]={
      'registered':len(group),'scored':n,'correct':n-len(wrong),'wrong':len(wrong),
      'unscorable':sum(e['status']=='unscorable' for e in group),
      'pending':sum(e['status']=='pending' for e in group),
      'awaiting_or_overdue':sum(e['status']=='awaiting_source_score' for e in group),
      'accuracy_pct':round(100*(n-len(wrong))/n,2) if n else None,
      'by_prediction_direction':by_direction,'wrong_factor_counts':by_factor}
 error_events=[e for e in by_track['original_ticker'] if e['category'] in
               ('direction_wrong','observation_coverage_failure','overdue_missing_score')]
 result={'schema_version':1,'status':'ok','generated_at_utc':now.isoformat(),
  'source':'Immutable DOGE V4.2 registered forecasts + official scored ticker and independent confirmed-1m scores',
  'study_effective_from_utc':freeze.isoformat(),
  'track_definitions':{'original_ticker':'only genuine quote in [target,target+25m]',
    'confirmed_1m_close':'separate first fully confirmed minute close after target, never exact quote'},
  'registered':len(reg),'original_quote_score_rows':len(original),
  'independent_minute_score_rows':len(minute),'cohort_statistics':cohorts,
  'errors_and_coverage_examples':error_events[-35:],
  'minute_wrong_diagnostic_examples':[e for e in by_track['confirmed_1m_close']
    if e['category']=='direction_wrong'][-12:],
  '24h_watch':forecast_watch[-12:],
  'hypothesis_queue':[{'candidate':'Review BTC and ETH co-movement as an ex-ante feature',
    'status':'NOT_AUTHORIZED_FOR_MODEL',
    'evidence_type':'post-event association; require strictly earlier first_seen timestamp',
    'unlock_only_after':'preregistered time-split benchmark plus new prospective A/B trial'},
   {'candidate':'Evaluate DOGE/BTC relative strength observed BEFORE forecast issuance',
    'status':'NOT_AUTHORIZED_FOR_MODEL',
    'evidence_type':'post-event correlation is not causal proof',
    'unlock_only_after':'independent out-of-sample improvement with preserved original baseline'},
   {'candidate':'Reduce late/missing quote scoring via separately frozen independent minute track',
    'status':'MEASUREMENT_ONLY_NOT_MODEL_CHANGE',
    'evidence_type':'observability and coverage, not predictive improvement'}],
  'coverage_failure_is_not_model_direction_error':True,
  'pre_freeze_diagnostic_excluded_from_70pct_validation':True,
  'retrospective_correlation_not_proven_cause':True,
  'historical_hourly_retrieval_not_evidence_of_exante_availability':True,
  'unverified_macro_etf_liquidation_causality_not_asserted':True,
  'automatic_model_change_authorized':False,
  'automatic_dca_change_authorized':False,
  'validated_70pct':False,'claim_permission':'NO_VALIDATED_EDGE'}
 return result

def main():
 OUTPUT.parent.mkdir(parents=True,exist_ok=True)
 try:result=evaluate()
 except Exception as exc:
  result={'schema_version':1,'status':'blocked',
   'generated_at_utc':datetime.now(timezone.utc).isoformat(),
   'error':type(exc).__name__+': '+str(exc)[:400],
   'claim_permission':'NO_VALIDATED_EDGE','validated_70pct':False}
 tmp=OUTPUT.with_suffix('.json.tmp')
 tmp.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 os.replace(tmp,OUTPUT)
 print(json.dumps({'status':result['status'],'registered':result.get('registered'),
  'quote_score_rows':result.get('original_quote_score_rows'),
  'error':result.get('error')}),flush=True)
 return 0 if result['status']=='ok' else 1

if __name__=='__main__':
 raise SystemExit(main())
