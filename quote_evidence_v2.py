#!/usr/bin/env python3
"""Capture and bind raw OKX ticker responses without changing frozen V4.2."""
import hashlib,json,math,sys,urllib.request
from datetime import datetime,timezone
from pathlib import Path
import direction_forward_v42 as frozen
import spot_plus_quote_watch as watch
from forward_validation import history_receipts
from forward_validation import git

ROOT=frozen.ROOT
URL='https://www.okx.com/api/v5/market/ticker?instId=DOGE-USDT'
BASE='data/quote_evidence_v2'
BINDINGS=BASE+'/bindings.csv'
BCOLS=['forecast_id','prediction_id','kind','quote_id']

def snapshot_path(root,qid):
 if len(qid)!=64 or any(c not in '0123456789abcdef' for c in qid):raise ValueError('invalid quote ID')
 return root/BASE/'snapshots'/qid[:2]/(qid+'.json')

def parse(payload):
 if payload['source_url']!=URL:raise ValueError('unexpected quote source')
 j=json.loads(payload['raw_response_utf8'])
 if j.get('code')!='0' or len(j.get('data',[]))!=1:raise ValueError('invalid OKX response')
 row=j['data'][0]
 if row.get('instId')!='DOGE-USDT':raise ValueError('wrong quote symbol')
 price=float(row['last']);quoted=datetime.fromtimestamp(int(row['ts'])/1000,timezone.utc)
 observed=frozen.utc(payload['observed_at_utc'])
 if not math.isfinite(price) or price<=0 or not 0<=(observed-quoted).total_seconds()<=120:raise ValueError('stale/future/invalid quote')
 return price,quoted,observed

def save_quote(root,raw,observed):
 payload={'schema_version':2,'source_url':URL,'observed_at_utc':observed.isoformat(),'raw_response_utf8':raw,
  'transport':'HTTPS collector observation; not exchange-signed and not an executable fill'}
 parse(payload)
 data=(json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(',',':'))+'\n').encode('utf-8')
 qid=hashlib.sha256(data).hexdigest();path=snapshot_path(root,qid);path.parent.mkdir(parents=True,exist_ok=True)
 try:
  with path.open('xb') as f:f.write(data)
 except FileExistsError:
  if path.read_bytes()!=data:raise ValueError('existing quote hash conflict')
 return qid

def load_quote(root,qid):
 path=snapshot_path(root,qid)
 if not path.exists():raise ValueError('raw quote missing')
 data=path.read_bytes()
 if hashlib.sha256(data).hexdigest()!=qid:raise ValueError('quote hash mismatch')
 return parse(json.loads(data))

def bind(rows,pid,kind,qid):
 key=pid+'|'+kind
 row={'forecast_id':key,'prediction_id':pid,'kind':kind,'quote_id':qid}
 if key in rows and rows[key]!=row:raise ValueError('immutable quote binding replaced')
 rows[key]=row

def verify_bound(root,row,score,bindings):
 for kind,clock,price in [('ISSUE',row['source_quote_utc'],row['reference_price'])]+(
   [('TARGET',score['target_quote_utc'],score['target_price'])] if score and score['status']=='scored' else []):
  binding=bindings.get(row['forecast_id']+'|'+kind)
  if not binding or binding['prediction_id']!=row['forecast_id'] or binding['kind']!=kind:raise ValueError('quote binding missing or invalid')
  actual,quoted,observed=load_quote(root,binding['quote_id'])
  if frozen.utc(clock)!=quoted:raise ValueError('bound quote timestamp mismatch')
  if format(actual,'.12g')!=format(float(price),'.12g'):raise ValueError('bound quote price mismatch')
  event=frozen.utc(row['issued_at_utc'] if kind=='ISSUE' else score['observed_at_utc'])
  if observed!=event:raise ValueError('bound observation time mismatch')

def verify_public_issue(root,row,bindings,head):
 binding=bindings.get(row['forecast_id']+'|ISSUE')
 if not binding:raise ValueError('issue quote binding missing')
 qid=binding['quote_id'];relative=snapshot_path(root,qid).relative_to(root).as_posix()
 try:body=git(root,'show',head+':'+relative)+'\n'
 except Exception as exc:raise ValueError('issue raw quote not present at pre-target public head') from exc
 if hashlib.sha256(body.encode('utf-8')).hexdigest()!=qid:raise ValueError('public issue quote hash mismatch')

def collect(root=ROOT,mode='watch'):
 with urllib.request.urlopen(urllib.request.Request(URL,headers={'Accept':'application/json','User-Agent':'DOGE-evidence-v2'}),timeout=20) as response:
  raw=response.read().decode('utf-8')
 now=datetime.now(timezone.utc);qid=save_quote(root,raw,now);price,quoted,_=load_quote(root,qid)
 regpath=root/'data/direction_v42/forward_registry.csv';scorepath=root/'data/direction_v42/forward_scores.csv'
 before_reg=frozen.read_rows(regpath,frozen.FCOLS);before_scores=frozen.read_rows(scorepath,frozen.SCOLS)
 bindings=frozen.read_rows(root/BINDINGS,BCOLS);history_receipts(root,BINDINGS,BCOLS,bindings)
 if mode=='watch':out=watch.run(root,now,(price,quoted))
 elif mode=='direction':out=frozen.run(root,now,(price,quoted))
 else:raise ValueError('unknown collector mode')
 after_reg=frozen.read_rows(regpath,frozen.FCOLS);after_scores=frozen.read_rows(scorepath,frozen.SCOLS)
 for pid in after_reg.keys()-before_reg.keys():bind(bindings,pid,'ISSUE',qid)
 for pid in after_scores.keys()-before_scores.keys():
  if after_scores[pid]['status']=='scored':bind(bindings,pid,'TARGET',qid)
 for pid in after_reg.keys()-before_reg.keys():verify_bound(root,after_reg[pid],None,bindings)
 if bindings!=frozen.read_rows(root/BINDINGS,BCOLS):frozen.write(root/BINDINGS,frozen.serial(bindings,BCOLS))
 if mode=='direction':
  out['legacy_metrics_exploratory_only']=True;out['official_score_report']='reports/DOGE_FORWARD_VALIDATION_V2.md'
  for horizon in out.get('horizons',{}).values():
   for model in horizon['models'].values():model['nonoverlap_fixed_phase']['exploratory_only_not_formal']=True
  frozen.write(root/'data/direction_v42/forward_latest.json',json.dumps(out,ensure_ascii=False,indent=2)+'\n')
 print(json.dumps({'status':out['status'],'raw_quote_id':qid,'bindings':len(bindings)}))
 return 0 if out['status']=='ok' else 1

if __name__=='__main__':raise SystemExit(collect(mode=sys.argv[1] if len(sys.argv)>1 else 'watch'))
