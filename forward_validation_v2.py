#!/usr/bin/env python3
"""Official V2 view: pinned implementation plus raw quote binding, future cohort only."""
import json,sys
from datetime import datetime,timedelta,timezone
import forward_validation as base
import direction_forward_v42 as frozen
import quote_evidence_v2 as evidence

ROOT=frozen.ROOT
PROTOCOL='data/stage5/forward_validation_protocol_v2.json'
WITNESS='data/stage5/forward_validation_v2/protocol_witness.json'
REQUIRED_CODE={'forward_validation_v2.py','quote_evidence_v2.py','forward_validation.py','stage5_forward_quality.py','stage5_70pct_audit.py','direction_forward_v42.py','direction_baseline_v42.py','spot_plus_quote_watch.py'}

def check_protocol(protocol):
 if (protocol.get('status')!='frozen_protocol' or protocol.get('model_id')!=frozen.MODEL
     or protocol.get('primary_horizons_hours')!=[1,24]
     or set(protocol.get('locked_code_git_blobs',{}))!=REQUIRED_CODE
     or protocol.get('minimum_quote_and_issuance_coverage_fraction')!=.9
     or protocol.get('minimum_nonoverlap_valid_scores')!=100):
  raise ValueError('invalid V2 protocol or incomplete implementation manifest')

def check_code(root,protocol):
 for path,expected in protocol['locked_code_git_blobs'].items():
  if base.git(root,'hash-object',path)!=expected:raise ValueError('frozen V2 implementation modified: '+path)

def issuance(rows,effective,now,h):
 interval=timedelta(hours=h);grace=timedelta(minutes=45) if h==1 else interval
 n=max(0,int((now-effective-grace).total_seconds()//interval.total_seconds())+1)
 seen={int((frozen.utc(r['issued_at_utc'])-effective).total_seconds()//interval.total_seconds()) for r in rows}
 issued=len(seen & set(range(n)))
 return {'expected':n,'issued':issued,'missing':n-issued,'coverage_pct':100*issued/n if n else None,
  'window_minutes':45 if h==1 else 1440}

def protocol_witness(root,protocol,runs,persist):
 text=(root/PROTOCOL).read_text(encoding='utf-8').strip();effective=frozen.utc(protocol['effective_from_utc'])
 path=root/WITNESS;found=json.loads(path.read_text()) if path.exists() else None
 if found:
  run=base.github_json('/actions/runs/'+str(found['run_id']))
  if run['head_sha']!=found['head_sha'] or frozen.utc(run['created_at'])!=frozen.utc(found['created_at']):raise ValueError('V2 protocol witness mismatch')
  candidates=[run]
 else:candidates=sorted(runs,key=lambda r:r['created_at'])
 for run in candidates:
  if frozen.utc(run['created_at'])>=effective:continue
  try:
   base.git(root,'merge-base','--is-ancestor',run['head_sha'],'HEAD')
   if base.git(root,'show',run['head_sha']+':'+PROTOCOL)!=text:continue
  except base.subprocess.CalledProcessError:continue
  found={'run_id':run['id'],'head_sha':run['head_sha'],'created_at':run['created_at']}
  if persist and not path.exists():frozen.write(path,json.dumps(found,sort_keys=True)+'\n')
  return found
 if found:raise ValueError('invalid V2 protocol witness')
 return None

def evaluate(root=ROOT,now=None,runs=None,persist=True):
 now=now or datetime.now(timezone.utc)
 p=json.loads((root/PROTOCOL).read_text());check_protocol(p);check_code(root,p)
 if base.locked_file(root,PROTOCOL)>=frozen.utc(p['effective_from_utc']):raise ValueError('V2 protocol committed after effective time')
 runs=base.fetch_runs() if runs is None else runs
 witness=protocol_witness(root,p,runs,persist)
 effective=frozen.utc(p['effective_from_utc'])
 if now>=effective and witness is None:raise ValueError('V2 not witnessed before start; new future version required')
 # V1 still runs untouched and supplies corroborated publication receipts.
 legacy=base.evaluate(root,now,runs,persist_witnesses=persist)
 registry=frozen.read_rows(root/base.REG,frozen.FCOLS);scores=frozen.read_rows(root/base.SCORES,frozen.SCOLS)
 bindings=frozen.read_rows(root/evidence.BINDINGS,evidence.BCOLS)
 base.history_receipts(root,evidence.BINDINGS,evidence.BCOLS,bindings)
 for key,b in bindings.items():
  if key!=b['prediction_id']+'|'+b['kind'] or b['prediction_id'] not in registry or b['kind'] not in ('ISSUE','TARGET'):raise ValueError('orphan/invalid quote binding')
  evidence.load_quote(root,b['quote_id'])
 accepted={};horizons={};rejected=[]
 public=legacy['verified_remote_head_witnesses']
 for h in (1,24):
  rows=base.selections(registry,effective,h)
  valid={};missing=[]
  for r in rows:
   fid=r['forecast_id'];s=scores.get(fid)
   try:evidence.verify_bound(root,r,s,bindings)
   except ValueError:
    missing.append(fid);continue
   w=public.get(fid)
   if w is None or frozen.utc(w['observed_public_utc'])>frozen.utc(r['issued_at_utc'])+timedelta(minutes=45) or frozen.utc(w['observed_public_utc'])>=frozen.utc(r['target_utc']):
    missing.append(fid);continue
   try:evidence.verify_public_issue(root,r,bindings,w['head_sha'])
   except ValueError:
    missing.append(fid);continue
   if s:valid[fid]=s;accepted[fid]=s
  m=base.compare(rows,valid,now);m['issuance']=issuance(rows,effective,now,h)
  m['missing_raw_quote_or_publication_ids']=missing
  m['formal_status']='尚无成熟样本' if not m['due'] else '覆盖率不足' if m['quote_coverage_pct']<90 or (m['issuance']['coverage_pct'] or 0)<90 else '继续积累独立样本，未验收预测优势'
  horizons[str(h)+'h']=m;rejected.extend(missing)
 day=base.selections(registry,effective,24)
 economic=base.paper(base.economic_cohort(day,effective,now),accepted,p['fee_fraction_each_side'],p['slippage_fraction_each_side'])
 snapshots=list((root/evidence.BASE/'snapshots').glob('*/*.json'))
 # Content-addressed files may never be edited or disappear while referenced.
 for path in snapshots:evidence.load_quote(root,path.stem)
 return {'schema_version':2,'status':'ok','audited_at_utc':now.isoformat(),'effective_from_utc':effective.isoformat(),
  'protocol_id':p['protocol_id'],'protocol_remote_witness':witness,'locked_implementation_checked':True,
  'raw_quote_snapshots':len(snapshots),'quote_bindings':len(bindings),'horizons':horizons,'prospective_spot_paper':economic,
  'official_score_source':'V2 only; V1 and frozen collector metrics are legacy exploratory diagnostics',
  'accuracy_70pct_validated':False,'economic_edge_validated':False,
  'external_immutable_storage_configured':False,'absolute_immutability_verified':False,
  'limitations':['Raw HTTPS responses are collector evidence, not exchange-signed evidence or executable fills',
    'GitHub administrators can rewrite/delete history and artifacts; independent retention-locked storage is not configured',
    'Paper fee/slippage assumptions and checkpoint drawdown cannot establish actual trading or intrainterval risk',
    '100 nonoverlapping valid scores, at least30days, confidence lower bound70%, baseline superiority and market-regime replication remain unverified']}

def main():
 if '--integrity-only' in sys.argv:
  p=json.loads((ROOT/PROTOCOL).read_text());check_protocol(p);check_code(ROOT,p);base.locked_file(ROOT,PROTOCOL)
  return base.main()
 daily='--daily' in sys.argv
 # Keep the V1 output as historical diagnostic, including its original flags.
 base.main()
 try:out=evaluate(persist=not daily)
 except Exception as exc:out={'status':'blocked','audited_at_utc':datetime.now(timezone.utc).isoformat(),'error':str(exc),'accuracy_70pct_validated':False,'economic_edge_validated':False}
 name='daily_latest' if daily else 'latest'
 frozen.write(ROOT/f'data/stage5/forward_validation_v2/{name}.json',json.dumps(out,ensure_ascii=False,indent=2)+'\n')
 verdict=('技术证据可核对；90%覆盖率、70%准确率与经济优势尚未验收。' if out['status']=='ok'
          else '证据或完整性检查失败，审计阻断；不得使用本次结果宣称达标。')
 lines=['# DOGE 正式前瞻验证 V2','',f"截止 UTC：{out['audited_at_utc']}",'','结论：'+verdict]
 if out['status']=='ok':
  lines+=['',f"生效 UTC：{out['effective_from_utc']}；原始报价快照 {out['raw_quote_snapshots']}；绑定 {out['quote_bindings']}。",
   '', '|期限|正式到期|证据完整评分|报价覆盖率|发行覆盖率|状态|','|---|---:|---:|---|---|---|']
  def val(x):return '尚无成熟样本' if x is None else f'{x:.2f}%'
  for h,m in out['horizons'].items():lines.append(f"|{h}|{m['due']}|{m['scored']}|{val(m['quote_coverage_pct'])}|{val(m['issuance']['coverage_pct'])}|{m['formal_status']}|")
  e=out['prospective_spot_paper'];lines+=['',f"完整现货纸面区间：{e['completed_intervals']}；策略净收益：{val(e['strategy_net_return_pct'])}；持币：{val(e['buy_hold_net_return_pct'])}；现金0%。",
   '费用每侧0.10%、滑点每侧0.05%；首次缺失或重叠后停止，已有收益仅代表完整前缀。',
   '正式不重叠成绩在JSON中预先选定；漏评不替换。V1与旧采集摘要只作探索诊断。',
   '', '独立外部不可改写存储尚未配置。原始响应来自采集器HTTPS观察，未获交易所数字签名。',
   '不下单、不修改定投规则、不宣称达标。']
 else:lines+=['',out['error']]
 report='DOGE_FORWARD_VALIDATION_V2_DAILY.md' if daily else 'DOGE_FORWARD_VALIDATION_V2.md'
 frozen.write(ROOT/'reports'/report,'\n'.join(lines)+'\n')
 print(json.dumps({'status':out['status'],'v2_protocol':out.get('protocol_id'),'error':out.get('error')}))
 return 0 if out['status']=='ok' else 1

if __name__=='__main__':raise SystemExit(main())
