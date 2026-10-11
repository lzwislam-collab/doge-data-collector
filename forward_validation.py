#!/usr/bin/env python3
"""Independent prospective evidence and spot paper accounting; never place orders."""
import csv
import hashlib
import io
import json
import math
import os
import subprocess
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from direction_forward_v42 import ROOT, FCOLS, SCOLS, MODEL, read_rows, utc, write
from stage5_forward_quality import evaluate as coverage_audit
from stage5_70pct_audit import wilson_lower

REG='data/direction_v42/forward_registry.csv'
SCORES='data/direction_v42/forward_scores.csv'
PROTOCOL='data/stage5/forward_validation_protocol.json'
OUT='data/stage5/forward_validation/latest.json'
WITNESSES='data/stage5/forward_validation/remote_witnesses.csv'
WCOLS=['forecast_id','run_id','observed_public_utc','head_sha','run_url']
RUN_CACHE='data/stage5/forward_validation/verified_run_metadata.csv'
RCOLS=['forecast_id','head_sha','created_at','first_verified_at_utc']

def git(root,*args):
 return subprocess.run(['git',*args],cwd=root,check=True,capture_output=True,text=True,encoding='utf-8').stdout.strip()

def history_receipts(root,relative,columns,current):
 """Check every reachable revision, including deletions. Retain first commit receipt."""
 if git(root,'rev-parse','--is-shallow-repository')!='false':
  raise ValueError('full git history required')
 revs=git(root,'log','--reverse','--format=%H','--',relative).splitlines()
 prior={};receipts={}
 for rev in revs:
  body=git(root,'show',rev+':'+relative)
  reader=csv.DictReader(io.StringIO(body))
  if reader.fieldnames!=columns:raise ValueError('historic schema revised: '+relative)
  snap={}
  for row in reader:
   fid=row['forecast_id']
   if not fid or fid in snap:raise ValueError('duplicate historical ID')
   snap[fid]=row
  if any(snap.get(k)!=v for k,v in prior.items()):
   raise ValueError('committed record revised: '+relative)
  stamp=git(root,'show','-s','--format=%cI',rev)
  for fid in snap.keys()-prior.keys():
   receipts[fid]={'commit_sha':rev,'committed_at_utc':datetime.fromisoformat(stamp).astimezone(timezone.utc).isoformat()}
  prior=snap
 if any(current.get(k)!=v for k,v in prior.items()):
  raise ValueError('current record revised: '+relative)
 return receipts

def locked_file(root,relative):
 revs=git(root,'log','--format=%H','--',relative).splitlines()
 if not revs:raise ValueError('protocol has no committed receipt: '+relative)
 bodies=[git(root,'show',rev+':'+relative) for rev in revs]
 if len(set(bodies+[ (root/relative).read_text(encoding='utf-8').strip() ]))!=1:
  raise ValueError('frozen protocol revised: '+relative)
 return utc(datetime.fromisoformat(git(root,'show','-s','--format=%cI',revs[-1])).astimezone(timezone.utc).isoformat())

def positive(v):
 x=float(v)
 if not math.isfinite(x) or x<=0:raise ValueError('invalid price')
 return x

def validate_scores(registry,scores,now):
 for fid,row in registry.items():
  issued=utc(row['issued_at_utc']);q=utc(row['source_quote_utc']);h=int(row['horizon_hours'])
  if row['model_id']!=MODEL or h not in (1,24,168,720) or utc(row['target_utc'])!=issued+timedelta(hours=h):
   raise ValueError('invalid forecast target/model')
  if issued>now or not 0<=(issued-q).total_seconds()<=120:raise ValueError('invalid issuance clock')
  positive(row['reference_price'])
  if any(row[k] not in ('UP','DOWN') for k in ('majority_direction','doge_reversal_direction','btc_momentum_direction')):
   raise ValueError('invalid forecast direction')
 for fid,s in scores.items():
  if fid not in registry:raise ValueError('orphan score')
  row=registry[fid];target=utc(row['target_utc']);obs=utc(s['observed_at_utc'])
  if obs>now:raise ValueError('future score')
  if s['status']=='unscorable_missing_target_quote':
   if obs<=target+timedelta(minutes=25) or any(s[k] for k in ('target_quote_utc','target_price','realized_up','majority_correct','doge_reversal_correct','btc_momentum_correct')):
    raise ValueError('invalid missing disposition')
   continue
  if s['status']!='scored':raise ValueError('unknown score status')
  q=utc(s['target_quote_utc'])
  if not target<=q<=target+timedelta(minutes=25) or not 0<=(obs-q).total_seconds()<=120:
   raise ValueError('invalid target quote window')
  true='UP' if positive(s['target_price'])>positive(row['reference_price']) else 'DOWN'
  if s['realized_up']!=true:raise ValueError('incorrect realized label')
  for m in ('majority','doge_reversal','btc_momentum'):
   if s[m+'_correct']!=str(int(row[m+'_direction']==true)):raise ValueError('incorrect score label')

def compare(rows,scores,now,include_nonoverlap=True):
 due=[r for r in rows if utc(r['target_utc'])+timedelta(minutes=25)<=now]
 valid=[(r,scores[r['forecast_id']]) for r in due if scores.get(r['forecast_id'],{}).get('status')=='scored']
 n=len(valid);rate=100*n/len(due) if due else None
 out={'due':len(due),'scored':n,'missing_or_unresolved':len(due)-n,'quote_coverage_pct':rate,
  'coverage_state':'NO_MATURED_SAMPLE' if rate is None else 'BELOW_90' if rate<90 else 'OBSERVED_AT_LEAST_90',
  'models':{},'paired_primary_vs_majority':{},'edge_validated':False}
 for m in ('doge_reversal','majority','btc_momentum','always_up','always_down'):
  flags=[int((r[m+'_direction'] if not m.startswith('always_') else m[7:].upper())==s['realized_up']) for r,s in valid]
  k=sum(flags)
  out['models'][m]={'n':n,'correct':k,'accuracy_pct':100*k/n if n else None,
   'wilson95_lower_pct_descriptive_only':100*wilson_lower(k,n) if n else None}
 wins=sum(s['doge_reversal_correct']=='1' and s['majority_correct']=='0' for _,s in valid)
 losses=sum(s['doge_reversal_correct']=='0' and s['majority_correct']=='1' for _,s in valid)
 out['paired_primary_vs_majority']={'wins':wins,'losses':losses,'ties':n-wins-losses,
  'accuracy_difference_pct_points':100*(wins-losses)/n if n else None,'same_quotes_and_same_rows':True}
 if include_nonoverlap:
  independent=[];reserved_until=None
  # Select before inspecting outcome availability/correctness, reserve full quote window.
  for r in sorted(rows,key=lambda r:(utc(r['issued_at_utc']),r['forecast_id'])):
   if reserved_until is None or utc(r['issued_at_utc'])>=reserved_until:
    independent.append(r);reserved_until=utc(r['target_utc'])+timedelta(minutes=25)
  out['all_bin_scores_descriptive_may_overlap']=True
  out['nonoverlap']=compare(independent,scores,now,False)
  out['nonoverlap']['selected_ids']=[r['forecast_id'] for r in independent]
  out['nonoverlap']['selection_rule']='earliest issuance, reserve target+25m before outcomes; never replace missing rows'
 return out

def paper(rows,scores,fee,slippage):
 if not 0<=fee<1 or not 0<=slippage<1:raise ValueError('invalid costs')
 equity=peak=1.;drawdown=0.;completed=[];blocked=None;last_exit=None
 factor=(1-slippage)*(1-fee)/((1+slippage)*(1+fee))
 for row in rows:
  fid=row['forecast_id'];s=scores.get(fid,{})
  if last_exit is not None and utc(row['issued_at_utc'])<last_exit:
   blocked=fid;break
  if s.get('status')!='scored':blocked=fid;break
  if row['doge_reversal_direction']=='UP':
   equity*=positive(s['target_price'])/positive(row['reference_price'])*factor
  peak=max(peak,equity);drawdown=max(drawdown,1-equity/peak)
  completed.append((row,s));last_exit=utc(s['target_quote_utc'])
 hold=(positive(completed[-1][1]['target_price'])/positive(completed[0][0]['reference_price'])*factor-1)*100 if completed else None
 return {'completed_intervals':len(completed),'blocked_forecast_id':blocked,
  'all_selected_intervals_complete':bool(rows) and blocked is None,
  'strategy_net_return_pct':100*(equity-1) if completed else None,
  'buy_hold_net_return_pct':hold,'cash_net_return_pct':0,
  'strategy_checkpoint_max_drawdown_pct':drawdown*100 if completed else None,
  'intervals':[{'forecast_id':r['forecast_id'],'entry_utc':r['issued_at_utc'],'exit_utc':s['target_quote_utc']} for r,s in completed],
  'economic_edge_validated':False,'paper_only_not_executable_fills':True,
  'drawdown_between_checkpoints_unmeasured':True,
  'strategy_rule':'24h first UTC-day forecast; UP long then liquidate, DOWN cash; cash between intervals; stop at first missing or overlapping interval'}

def selections(registry,effective,h):
 groups={}
 for row in registry.values():
  issued=utc(row['issued_at_utc'])
  if int(row['horizon_hours'])!=h or issued<effective:continue
  bin_id=int((issued-effective).total_seconds()//(h*3600))
  groups.setdefault(bin_id,[]).append(row)
 return [min(groups[k],key=lambda r:(utc(r['issued_at_utc']),r['forecast_id'])) for k in sorted(groups)]

def economic_cohort(rows,effective,now):
 groups={int((utc(r['issued_at_utc'])-effective).total_seconds()//86400):r for r in rows}
 out=[]
 # Absent bins become economically mature only after every possible issue+24h+25m expires.
 missing_due_days=max(0,int((now-effective-timedelta(hours=24,minutes=25)).total_seconds()//86400))
 count=max(missing_due_days,max(groups,default=-1)+1)
 for i in range(count):
  row=groups.get(i)
  if row is None:
   if i<missing_due_days:
    out.append({'forecast_id':'MISSING_ISSUANCE_'+(effective+timedelta(days=i)).date().isoformat(),
                'issued_at_utc':(effective+timedelta(days=i)).isoformat()})
   break
  if utc(row['target_utc'])+timedelta(minutes=25)>now:break
  out.append(row)
 return out

def remote_witnesses(root,registry,prior,runs,persist_cache=False):
 out=dict(prior)
 protocol_text=(root/PROTOCOL).read_text(encoding='utf-8').strip() if (root/PROTOCOL).exists() else None
 server_runs={str(r['id']):r for r in runs}
 cache=read_rows(root/RUN_CACHE,RCOLS)
 history_receipts(root,RUN_CACHE,RCOLS,cache)
 missing_ids={w['run_id'] for w in prior.values()}-server_runs.keys()
 unknown=missing_ids-cache.keys()
 old=sorted(missing_ids & cache.keys())
 # Bound remote rechecks; retain immutable first-corroborated metadata in between.
 offset=(int(datetime.now(timezone.utc).timestamp())//1200*16)%len(old) if old else 0
 recheck={old[(offset+i)%len(old)] for i in range(min(16,len(old)))}
 fetch_ids=unknown|recheck
 if fetch_ids:
  from concurrent.futures import ThreadPoolExecutor
  with ThreadPoolExecutor(max_workers=8) as pool:
   fetched=list(pool.map(lambda rid:github_json('/actions/runs/'+rid),sorted(fetch_ids)))
  server_runs.update({str(r['id']):r for r in fetched})
 for rid in missing_ids-fetch_ids:
  server_runs[rid]={'id':int(rid),'head_sha':cache[rid]['head_sha'],'created_at':cache[rid]['created_at']}
 for rid,run in server_runs.items():
  if rid in cache and (cache[rid]['head_sha']!=run['head_sha'] or utc(cache[rid]['created_at'])!=utc(run['created_at'])):
   raise ValueError('GitHub run metadata revised since first corroboration')
 for fid,w in prior.items():
  run=server_runs.get(w['run_id'])
  if not run or run['head_sha']!=w['head_sha'] or utc(run['created_at'])!=utc(w['observed_public_utc']):
   raise ValueError('persisted remote witness does not match GitHub run')
  git(root,'merge-base','--is-ancestor',w['head_sha'],'HEAD')
  if w['run_url']!=f"https://github.com/lzwislam-collab/doge-data-collector/actions/runs/{w['run_id']}":
   raise ValueError('invalid witness URL')
  stamp=utc(w['observed_public_utc'])
  if fid=='__PROTOCOL__':
   if protocol_text is None or git(root,'show',w['head_sha']+':'+PROTOCOL)!=protocol_text or stamp>=utc(json.loads(protocol_text)['effective_from_utc']):
    raise ValueError('invalid remote protocol witness')
  else:
   row=registry.get(fid)
   snap={r['forecast_id']:r for r in csv.DictReader(io.StringIO(git(root,'show',w['head_sha']+':'+REG)))}
   if row is None or snap.get(fid)!=row or not utc(row['issued_at_utc'])-timedelta(seconds=1)<=stamp<utc(row['target_utc']):
    raise ValueError('invalid persisted forecast witness')
 for run in sorted(runs,key=lambda r:r['created_at']):
  stamp=utc(run['created_at']);head=run['head_sha']
  try:
   git(root,'merge-base','--is-ancestor',head,'HEAD')
   body=git(root,'show',head+':'+REG)
  except subprocess.CalledProcessError:continue
  if protocol_text is not None and '__PROTOCOL__' not in out:
   try:remote_protocol=git(root,'show',head+':'+PROTOCOL)
   except subprocess.CalledProcessError:remote_protocol=None
   if remote_protocol==protocol_text and stamp<utc(json.loads(protocol_text)['effective_from_utc']):
    out['__PROTOCOL__']={'forecast_id':'__PROTOCOL__','run_id':str(run['id']),
      'observed_public_utc':stamp.isoformat(),'head_sha':head,
      'run_url':f"https://github.com/lzwislam-collab/doge-data-collector/actions/runs/{run['id']}"}
  for row in csv.DictReader(io.StringIO(body)):
   fid=row['forecast_id']
   if fid in out or registry.get(fid)!=row:continue
   if utc(row['issued_at_utc'])-timedelta(seconds=1)<=stamp<utc(row['target_utc']):
    out[fid]={'forecast_id':fid,'run_id':str(run['id']),'observed_public_utc':stamp.isoformat(),
     'head_sha':head,'run_url':f"https://github.com/lzwislam-collab/doge-data-collector/actions/runs/{run['id']}"}
 if persist_cache:
  for w in out.values():
   rid=w['run_id']
   if rid not in cache:
    run=server_runs[rid]
    cache[rid]={'forecast_id':rid,'head_sha':run['head_sha'],'created_at':run['created_at'],
      'first_verified_at_utc':datetime.now(timezone.utc).isoformat()}
  from direction_forward_v42 import serial
  write(root/RUN_CACHE,serial(cache,RCOLS))
 return out

def github_json(suffix):
 headers={'Accept':'application/vnd.github+json','User-Agent':'DOGE-forward-evidence-v1'}
 token=os.environ.get('GITHUB_TOKEN')
 if token:headers['Authorization']='Bearer '+token
 req=urllib.request.Request('https://api.github.com/repos/lzwislam-collab/doge-data-collector'+suffix,headers=headers)
 with urllib.request.urlopen(req,timeout=20) as response:return json.load(response)

def fetch_runs():return github_json('/actions/runs?per_page=100')['workflow_runs']

def evaluate(root=ROOT,now=None,runs=None,persist_witnesses=True):
 now=now or datetime.now(timezone.utc)
 if now.tzinfo is None or now.utcoffset()!=timedelta(0):raise ValueError('audit clock must be UTC')
 protocol=json.loads((root/PROTOCOL).read_text())
 effective=utc(protocol['effective_from_utc'])
 committed=locked_file(root,PROTOCOL)
 if committed>=effective:raise ValueError('economic protocol published after start; new future protocol required')
 for p in ('data/stage5/pre_registration.json','data/stage5/minute_evaluation_protocol.json'):
  locked_file(root,p)
 freeze=json.loads((root/'data/stage5/pre_registration.json').read_text())
 if git(root,'hash-object',freeze['locked_source_file'])!=freeze['locked_source_git_blob_sha']:
  raise ValueError('frozen prediction implementation modified')
 registry=read_rows(root/REG,FCOLS);scores=read_rows(root/SCORES,SCOLS)
 validate_scores(registry,scores,now)
 receipts=history_receipts(root,REG,FCOLS,registry)
 history_receipts(root,SCORES,SCOLS,scores)
 prior_witnesses=read_rows(root/WITNESSES,WCOLS)
 history_receipts(root,WITNESSES,WCOLS,prior_witnesses)
 witnesses=remote_witnesses(root,registry,prior_witnesses,fetch_runs() if runs is None else runs,persist_witnesses)
 if now>=effective and '__PROTOCOL__' not in witnesses:
  raise ValueError('no verified remote protocol witness before effective time; new future cohort required')
 quality=coverage_audit(root,now)
 horizons={};excluded=[]
 for h in (1,24):
  rows=selections(registry,utc(freeze['effective_from_utc']),h)
  accepted=[]
  for r in rows:
   receipt=receipts.get(r['forecast_id'])
   publish=utc(receipt['committed_at_utc']) if receipt else None
   issued=utc(r['issued_at_utc'])
   # Git commit timestamps have whole-second resolution; issuance has microseconds.
   witness=witnesses.get(r['forecast_id'])
   if publish is None or not issued-timedelta(seconds=1)<=publish<=issued+timedelta(minutes=protocol['max_publication_delay_minutes']) or publish>=utc(r['target_utc']) or witness is None or utc(witness['observed_public_utc'])>=utc(r['target_utc']) or utc(witness['observed_public_utc'])>issued+timedelta(minutes=protocol['max_publication_delay_minutes']):
    excluded.append(r['forecast_id'])
   else:accepted.append(r)
  eligible_scores={k:v for k,v in scores.items() if k in {r['forecast_id'] for r in accepted}}
  metrics=compare(rows,eligible_scores,now)
  # Excluded publication still enters issued/due denominator: never hide late evidence.
  all_due=sum(utc(r['target_utc'])+timedelta(minutes=25)<=now for r in rows)
  metrics['due']=all_due;metrics['missing_or_unresolved']=all_due-metrics['scored']
  rate=100*metrics['scored']/all_due if all_due else None
  metrics['quote_coverage_pct']=rate
  metrics['coverage_state']='NO_MATURED_SAMPLE' if rate is None else 'BELOW_90' if rate<90 else 'OBSERVED_AT_LEAST_90'
  metrics['issuance']=quality['horizons'][str(h)+'h']
  metrics['acceptance']='INSUFFICIENT_EVIDENCE'
  if rate is not None and (rate<90 or (metrics['issuance']['issuance_coverage_pct'] or 0)<90):metrics['acceptance']='COVERAGE_FAILED'
  metrics['minimum_valid_samples']=100
  horizons[str(h)+'h']=metrics
 econ_rows=selections(registry,effective,24)
 econ=economic_cohort(econ_rows,effective,now)
 econ_scores={k:v for k,v in scores.items() if k not in excluded}
 economics=paper(econ,econ_scores,protocol['fee_fraction_each_side'],protocol['slippage_fraction_each_side'])
 economics['effective_from_utc']=effective.isoformat()
 economics['registered_daily_intervals']=len(econ_rows)
 closed_days=max(0,int((now-effective).total_seconds()//86400))
 issued_closed_days=len({utc(r['issued_at_utc']).date() for r in econ_rows if utc(r['issued_at_utc'])<effective+timedelta(days=closed_days)})
 economics['missing_issuance_days']=closed_days-issued_closed_days
 economics['missing_issuance_days']=max(0,economics['missing_issuance_days'])
 out={'schema_version':1,'status':'ok','audited_at_utc':now.isoformat(),'input_commit_sha':git(root,'rev-parse','HEAD'),
  'protocol_id':protocol['protocol_id'],'protocol_remote_publication_verified': '__PROTOCOL__' in witnesses,
  'protocol_sha256':hashlib.sha256((root/PROTOCOL).read_bytes()).hexdigest(),
  'registry_sha256':hashlib.sha256((root/REG).read_bytes()).hexdigest(),
  'scores_sha256':hashlib.sha256((root/SCORES).read_bytes()).hexdigest(),
  'full_reachable_history_checked':True,'commit_receipts_not_publication_times':receipts,
  'verified_remote_head_witnesses':witnesses,'excluded_publication_ids':excluded,
  'horizons':horizons,'prospective_spot_paper':economics,
  'accuracy_70pct_validated':False,'economic_edge_validated':False,
  'limitations':['Git history and Actions artifacts are not independent WORM storage; administrators can rewrite/delete them',
   'Previously corroborated run metadata is cached append-only; at most 16 older runs are rechecked remotely per audit; local ancestor and content checks still run for every witness',
   'Commit clocks are audit evidence, not a trusted external timestamp authority',
   'Ticker snapshots with fixed fee/slippage assumptions are not actual fills',
   'Checkpoint drawdown omits intrainterval risk; no full drawdown acceptance',
   'No automatic promotion; need sufficient samples, baseline superiority and multiple market regimes']}
 if persist_witnesses and witnesses!=prior_witnesses:
  from direction_forward_v42 import serial
  write(root/WITNESSES,serial(witnesses,WCOLS))
 return out

def main():
 import sys
 if '--integrity-only' in sys.argv:
  registry=read_rows(ROOT/REG,FCOLS);scores=read_rows(ROOT/SCORES,SCOLS)
  history_receipts(ROOT,REG,FCOLS,registry);history_receipts(ROOT,SCORES,SCOLS,scores)
  history_receipts(ROOT,WITNESSES,WCOLS,read_rows(ROOT/WITNESSES,WCOLS))
  history_receipts(ROOT,RUN_CACHE,RCOLS,read_rows(ROOT/RUN_CACHE,RCOLS))
  for p in (PROTOCOL,'data/stage5/pre_registration.json','data/stage5/minute_evaluation_protocol.json'):locked_file(ROOT,p)
  validate_scores(registry,scores,datetime.now(timezone.utc))
  freeze=json.loads((ROOT/'data/stage5/pre_registration.json').read_text())
  if git(ROOT,'hash-object',freeze['locked_source_file'])!=freeze['locked_source_git_blob_sha']:raise ValueError('frozen model modified')
  print('Full committed forecast/score history and frozen protocols verified')
  return 0
 daily='--daily' in sys.argv
 try:out=evaluate(persist_witnesses=not daily)
 except Exception as e:
  out={'status':'blocked','audited_at_utc':datetime.now(timezone.utc).isoformat(),'error':str(e),
   'accuracy_70pct_validated':False,'economic_edge_validated':False}
 output='data/stage5/forward_validation/daily_latest.json' if daily else OUT
 write(ROOT/output,json.dumps(out,ensure_ascii=False,indent=2)+'\n')
 lines=['# DOGE 前瞻验证与现货纸面收益','',f"截止 UTC：{out['audited_at_utc']}",
  '',f"运行状态：{out['status']}；90%覆盖率、70%准确率与经济优势均须单独验收。"]
 if out['status']=='ok':
  lines.extend(['','|期限|正式到期|有效原始报价|报价覆盖率|发行覆盖率|验收|', '|---|---:|---:|---:|---:|---|'])
  for h,m in out['horizons'].items():
   lines.append(f"|{h}|{m['due']}|{m['scored']}|{m['quote_coverage_pct']}|{m['issuance']['issuance_coverage_pct']}|{m['acceptance']}|")
  lines.extend(['','所有基准使用同一组正式预测和同一报价；缺失仍进入覆盖率分母。',
   '逐模型准确率、配对胜负、首次提交凭据和输入哈希见 JSON。',
   '', '## 固定成本前瞻现货纸面验证', '',
   json.dumps(out['prospective_spot_paper'],ensure_ascii=False,indent=2),
   '', '费用每侧0.10%，滑点每侧0.05%；仅研究假设，非实际成交。',
   '出现首次漏评、漏发行或区间重叠即停止累计，已有收益仅代表完整前缀。',
   '持币基准覆盖同一首尾时间；现金0%；区间内回撤尚未测量。',
   '', '限制：GitHub管理员可改写历史或删除归档；尚无独立外部不可改写存证。',
   '正式样本不足，不声称达标，不交易，不修改定投规则。'])
 else:lines.extend(['',out['error']])
 report='reports/DOGE_FORWARD_VALIDATION_DAILY.md' if daily else 'reports/DOGE_FORWARD_VALIDATION.md'
 write(ROOT/report,'\n'.join(lines)+'\n')
 print(json.dumps({k:out[k] for k in ('status','accuracy_70pct_validated','economic_edge_validated')}))
 return 0 if out['status']=='ok' else 1

if __name__=='__main__':raise SystemExit(main())
