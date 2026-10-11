#!/usr/bin/env python3
"""Stage 5 prospective reliability audit: count missed issuance bins and missed scores.

No future predictions, no historical reconstruction and no revision of either
frozen scoring protocol. Strict fixed UTC bins, independent metrics by horizon
and outcome source. This report never authorizes a 70%-accuracy claim.
"""
import csv
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from stage5_70pct_audit import utc, wilson_lower

ROOT=Path(__file__).resolve().parent
MODEL='DOGE_DIRECTION_ISSUED_QUOTE_SHADOW_V42_1'
ALLOWED_H=(1,24)
ISSUE_GRACE=timedelta(minutes=45)
ORIGINAL_SCORE_GRACE=timedelta(minutes=25)
MINUTE_SCORE_GRACE=timedelta(minutes=2)


def table(root,relative,keys):
 path=root/relative
 if not path.exists():
  raise ValueError('required prospective data missing: '+relative)
 with path.open(newline='',encoding='utf-8-sig') as stream:
  source=csv.DictReader(stream)
  if not set(keys).issubset(source.fieldnames or []):
   raise ValueError('missing columns in '+relative)
  rows=list(source)
 seen=set()
 for row in rows:
  fid=row.get('forecast_id')
  if not fid or fid in seen:raise ValueError('empty/duplicate forecast ID in '+relative)
  seen.add(fid)
 return {row['forecast_id']:row for row in rows}


def measure(rows,model):
 n=len(rows)
 k=sum(int(s[model+'_correct']) for _,s in rows)
 pos=[s for _,s in rows if s['realized_up']=='UP']
 neg=[s for _,s in rows if s['realized_up']=='DOWN']
 return {'n':n,'correct':k,'accuracy_pct':round(100*k/n,2) if n else None,
  'wilson95_lower_pct_descriptive_only':round(100*wilson_lower(k,n),2) if n else None,
  'up_recall_pct':round(100*sum(int(s[model+'_correct']) for s in pos)/len(pos),2) if pos else None,
  'down_recall_pct':round(100*sum(int(s[model+'_correct']) for s in neg)/len(neg),2) if neg else None}


def evaluate(root=ROOT,now=None):
 if now is None:now=datetime.now(timezone.utc)
 if now.tzinfo is None or now.utcoffset()!=timedelta(0):
  raise ValueError('audit clock must use UTC')
 freeze=json.loads((root/'data/stage5/pre_registration.json').read_text())
 minute=json.loads((root/'data/stage5/minute_evaluation_protocol.json').read_text())
 if (freeze.get('status')!='frozen_protocol' or minute.get('status')!='frozen_protocol'
     or freeze.get('model_id')!=MODEL or minute.get('forecast_source_model_id')!=MODEL
     or freeze.get('primary_horizons_hours')!=[1,24]
     or minute.get('primary_horizons_hours')!=[1,24]
     or freeze['effective_from_utc']!=minute['effective_from_utc']
     or freeze['minimum_nonoverlapping_valid_scores_per_horizon']!=100
     or minute['minimum_nonoverlap_scored_per_horizon']!=100):
  raise ValueError('forward protocols inconsistent or modified')
 effective=utc(freeze['effective_from_utc'])
 registry=table(root,'data/direction_v42/forward_registry.csv',
  ('forecast_id','model_id','issued_at_utc','source_hour_start_utc','horizon_hours','target_utc'))
 quote=table(root,'data/direction_v42/forward_scores.csv',
  ('forecast_id','status','majority_correct','doge_reversal_correct','realized_up'))
 bars=table(root,'data/stage5/minute_shadow/minute_scores.csv',
  ('forecast_id','status','evaluation_cohort','majority_correct','doge_reversal_correct','realized_up'))
 if any(key not in registry for key in quote) or any(key not in registry for key in bars):
  raise ValueError('orphan score in forward registry')
 for row in registry.values():
  if row['model_id']!=MODEL:raise ValueError('unexpected model version')
  issued=utc(row['issued_at_utc']);target=utc(row['target_utc'])
  h=int(row['horizon_hours'])
  if target!=issued+timedelta(hours=h):
   raise ValueError('forecast target mismatch')
 report={'schema_version':1,'status':'ok','audited_at_utc':now.isoformat(),
  'effective_from_utc':effective.isoformat(),
  'protocol_id':freeze['protocol_id'],
  'minute_protocol_id':minute['protocol_id'],
  'frozen_issuance_deadline_minutes':45,
  'original_quote_score_deadline_minutes':25,
  'minute_close_score_deadline_minutes':2,
  'issued_predictions_not_backfilled':True,
  'expected_issuance_window_rule':'fixed UTC bins since protocol freeze, count after 45min window closes',
  'minute_outcome_not_comparable_to_exact_ticker':True,
  'not_verified_70pct':True,'claim_permission':'NO_VALIDATED_EDGE','horizons':{}}
 for h in ALLOWED_H:
  interval=timedelta(hours=h)
  groups={}
  eligible=[r for r in registry.values() if int(r['horizon_hours'])==h
            and utc(r['issued_at_utc'])>=effective]
  for row in eligible:
   issued=utc(row['issued_at_utc'])
   offset=int((issued-effective).total_seconds()//interval.total_seconds())
   boundary=effective+interval*offset
   if not boundary<=issued<=boundary+ISSUE_GRACE:
    raise ValueError('late/out-of-bin issued forecast: '+row['forecast_id'])
   if utc(row['source_hour_start_utc'])!=boundary:
    # For h=24, source_hour_start is the actual hourly issuance bar within that UTC day,
    # not necessarily midnight; only require consistent hour-aligned timing.
    if h==1 or not(boundary<=utc(row['source_hour_start_utc'])<boundary+interval):
     raise ValueError('source feature hour not in issuance bin')
   groups.setdefault(offset,[]).append(row)
  maturity=now-ISSUE_GRACE
  total=0
  if maturity>=effective:
   total=int((maturity-effective).total_seconds()//interval.total_seconds())+1
  issued_bins=[]
  missed=[]
  selections=[]
  for i in range(total):
   matching=groups.get(i,[])
   if not matching:
    missed.append((effective+interval*i).isoformat())
   else:
    chosen=min(matching,key=lambda r:utc(r['issued_at_utc']))
    issued_bins.append(i)
    selections.append(chosen)
  # Current open issuance bin never enters the denominator.
  quote_due=[r for r in selections if utc(r['target_utc'])+ORIGINAL_SCORE_GRACE<=now]
  minute_due=[r for r in selections if utc(r['target_utc'])+MINUTE_SCORE_GRACE<=now]
  valid_quote=[(r,quote[r['forecast_id']]) for r in quote_due if r['forecast_id'] in quote and quote[r['forecast_id']]['status']=='scored']
  valid_minute=[(r,bars[r['forecast_id']]) for r in minute_due if r['forecast_id'] in bars
    and bars[r['forecast_id']]['status']=='scored_from_confirmed_1m_close'
    and bars[r['forecast_id']]['evaluation_cohort']=='preregistered_forward_shadow']
  summary={'expected_expired_issue_windows':total,'issued_windows':len(issued_bins),
   'missing_issuance_windows':len(missed),
   'first_missing_issue_windows_utc':missed[:12],
   'issuance_coverage_pct':round(100*len(issued_bins)/total,2) if total else None,
   'all_postfreeze_registration_count':len(eligible),
   'extra_registrations_in_same_bin':sum(max(len(v)-1,0) for v in groups.values()),
   'quote_scoring':{'due_after_25min':len(quote_due),
                    'successfully_scored':len(valid_quote),
                    'missed_or_unscorable':len(quote_due)-len(valid_quote),
                    'coverage_pct':round(100*len(valid_quote)/len(quote_due),2) if quote_due else None,
                    'primary_model':measure(valid_quote,'doge_reversal'),
                    'majority_baseline':measure(valid_quote,'majority')},
   'minute_scoring':{'due_after_2min':len(minute_due),
                     'successfully_scored':len(valid_minute),
                     'missing':len(minute_due)-len(valid_minute),
                     'coverage_pct':round(100*len(valid_minute)/len(minute_due),2) if minute_due else None,
                     'primary_model':measure(valid_minute,'doge_reversal'),
                     'majority_baseline':measure(valid_minute,'majority')},
   'minimum_issuance_and_outcome_coverage_pct':90,
   'minimum_valid_nonoverlap_n':100,
   'primary_model_prospectively_proven_70pct':False}
  report['horizons'][str(h)+'h']=summary
 return report
