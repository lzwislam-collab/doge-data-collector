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
def main():
 stamp=datetime.now(timezone.utc).isoformat()
 result={'schema_version':1,'evaluated_at_utc':stamp,'status':'error',
         'claim_permission':'BLOCKED','research_only':True,'does_not_change_dca':True,
         'model_edge_proven':False,'checks':{},'errors':[]}
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
    'No change to DOGE investment execution rules']
 except Exception as e:result['errors'].append(type(e).__name__+': '+str(e)[:250])
 OUT.parent.mkdir(parents=True,exist_ok=True)
 temp=OUT.with_suffix('.json.tmp')
 temp.write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
 os.replace(temp,OUT)
 print(json.dumps({'status':result['status'],'claim_permission':result['claim_permission'],'errors':result['errors']}))
 return 0 if result['status']=='ok' else 1
if __name__=='__main__':raise SystemExit(main())
