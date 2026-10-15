"""Read-only PLUS projection of frozen actual journals. Never issues forecasts."""
import argparse,html,json,math,os,subprocess
from datetime import datetime,timedelta,timezone
from pathlib import Path
import learned_shadow_runtime as legacy
import phase2_forward_runtime_v2 as r2
import phase2_forward_v2 as f
import forward_readiness as operations
import prediction_release_control as releases

ROOT=Path(__file__).resolve().parent
JAPAN=timezone(timedelta(hours=9))

def forecast_row(track,h,e,s,prediction_verified,score_verified,now):
 if track not in ('original18','r2') or h not in (1,24) or track=='r2' and h!=1:raise ValueError('unregistered PLUS track/horizon')
 p=f.policy() if track=='r2' else json.loads((ROOT/f.old.POLICY).read_bytes())
 row={'track':track,'horizon_hours':h,'effective_utc':p['effective_from_utc'],'status':'WAIT_ACTIVATION' if now<f.utc(p['effective_from_utc']) else 'NO_FORMAL_ISSUANCE',
  'event_id':None,'direction':None,'probability_up':None,'calibration_verified':False,'active_forecast':False,'prediction_verified':False,'score_verified':False,
  'latest_same_event':{'sample_count':0,'accuracy':None,'brier':None,'net_return':None,'stress_net_return':None,'baselines':{}},
  'sample_scope':'latest event only; not full-cohort accuracy','baseline_scope':'same-event frozen replay, not original production issuance','real_orders_authorized':False}
 if e is None:return row
 if track=='r2' and (e.get('cohort')!='formal' or not e.get('included_in_formal_statistics',False)):raise ValueError('engineering canary is not a formal forecast')
 issued=f.utc(e['issued_at_utc']);target=f.utc(e['target_utc'])
 if issued>now or target!=issued+timedelta(hours=h) or (e.get('horizon_hours')!=h if track=='original18' else e.get('horizon_seconds')!=h*3600):raise ValueError('future or mismatched-horizon event')
 row.update(event_id=e['event_id'],issued_at_utc=e['issued_at_utc'],target_utc=e['target_utc'],event_sha256=f.digest(e),historical_record=now>=target)
 if not prediction_verified:row['status']='UNVERIFIED_PUBLICATION';return row
 prob=e['candidate_p']['selected' if track=='r2' else 'full']
 if isinstance(prob,bool) or not math.isfinite(prob) or not 0<=prob<=1:raise ValueError('invalid frozen probability')
 row.update(probability_up=prob,direction='UP' if prob>=.5 else 'DOWN',prediction_verified=True,active_forecast=now<target)
 deadline=f.deadline(e,p) if track=='r2' else legacy.shadow.target_deadline(e,p)
 row['status']='PENDING_TARGET' if now<target else 'TARGET_WINDOW_OPEN' if now<deadline else 'MISSING_TARGET'
 if s is not None and not score_verified:row['status']='TARGET_EVIDENCE_PENDING' if now<deadline else 'TARGET_EVIDENCE_MISSING'
 if s is None or not score_verified:return row
 if now<target:raise ValueError('score before target')
 y=s['y']
 if type(y) is not int or y not in (0,1):raise ValueError('invalid target direction')
 stats=row['latest_same_event'];stats.update(sample_count=1,accuracy=int(int(prob>=.5)==y),brier=(prob-y)**2)
 if track=='r2':
  costs=s.get('economic_returns',{}) if s.get('cost_error') is None else {};selected=costs.get('selected')
  stats['baselines']={name:{'accuracy':int(int(q>=.5)==y),'brier':(q-y)**2,**(costs.get(name) or {'net_return':None,'stress_net_return':None})} for name,q in e['candidate_p'].items() if name!='selected'}
  stats['baselines'].update({name:costs.get(name) or {'net_return':None,'stress_net_return':None} for name in ('frozen_rule','hold','cash')})
  if selected:stats.update(net_return=selected['net_return'],stress_net_return=selected['stress_net_return'])
 else:
  q=e['paired_p'];stats['baselines']={'frozen_majority':{'accuracy':int(int(q>=.5)==y),'brier':(q-y)**2},'frozen_rule':{'accuracy':int((e['frozen_direction']=='UP')==bool(y))}}
  deltas=s['economic_deltas']['full']
  if deltas is not None:
   stats.update(net_return=deltas['cash_net'],stress_net_return=deltas['cash_stress_net'])
   stats['same_event_cost_excess']=deltas
 row.update(score_verified=True,status='SCORED_PUBLICLY_VERIFIED',active_forecast=False)
 return row

def original_proofs(p,package,files,events,scores,now,online):
 if not online or not events:return {},{}
 bindings=legacy.read_bindings(ROOT);ids=[]
 for e in events:
  receipt=ROOT/legacy.BASE/'runtime_receipts'/(e['event_id']+'.json')
  if receipt.exists():ids.extend(json.loads(receipt.read_bytes())['witness_run_ids'])
  for kind in ('prediction','target'):
   if e['event_id']+'_'+kind in bindings:ids.append(bindings[e['event_id']+'_'+kind]['run_id'])
 runs=legacy.runs_from_server(ids)
 _,pred_rows,pred_rejected=legacy.shadow.verified_rows(p,package,events,[],files,runs,legacy.read_at,now)
 _,score_rows,rejected=legacy.shadow.verified_rows(p,package,events,scores,files,runs,legacy.read_at,now)
 pred={q['event_id'] for values in pred_rows.values() for q in values if q['group']=='full'}
 scored={q['event_id'] for values in score_rows.values() for q in values if q['group']=='full' and q['y'] is not None}
 return {e['event_id']:(e['event_id'] in pred,e['event_id'] in scored) for e in events},{**pred_rejected,**rejected}

def r2_proofs(p,files,events,scores,now,online):
 if not online or not events:return {},{}
 ids=[e[k] for e in events for k in ('registration_run_id','source_run_id')]
 for e in events:
  for kind in ('prediction','target'):
   path=ROOT/f.BASE/'bindings'/(e['event_id']+'_'+kind+'.json')
   if path.exists():ids.append(json.loads(path.read_bytes())['run_id'])
 runs=r2.runs_server(ids);scored,rejected=r2.publication(p,events,scores,files,runs);good={q['event_id'] for q in scored if q['formal_eligible']}
 proofs={}
 for e in events:
  try:
   r2.provenance(e,files,runs)
   pred=f.witness({**files,f.BASE+'/events/'+e['event_id']+'.json':f.encode(e)},runs,r2.read_at,f.utc(e['target_utc']),f.utc(e['issued_at_utc']))
   proofs[e['event_id']]=(pred is not None,e['event_id'] in good)
  except ValueError as exc:rejected[e['event_id']]=str(exc)
 return proofs,rejected

def selection_state(online,now):
 if online:return releases.select(ROOT,now)
 return {'selection':None,'reason':'OFFLINE_AUTHORITY_NOT_RECHECKED','real_orders_authorized':False}

def assessment_panel(assessment_run_id,online,current_event_count):
 performance={'status':'NO_CURRENT_INDEPENDENT_FULL_ASSESSMENT','accuracy':None,'accuracy_lower':None,'net_return_mean':None,'stress_net_return_mean':None,'qualified_formal_events':None,'validated_70pct':False}
 if assessment_run_id is not None:
  if not online:raise ValueError('independent assessment requires actual remote readback')
  try:
   a,raw,sha,_=releases.review_source(assessment_run_id)
   if a.get('mode')!='full' or not a['forward_runtime']['network_audit_covers_all_events']:raise ValueError('complete assessment required')
   # A verified old assessment is an as-of result, never claimed current.
   performance.update(status='INDEPENDENT_FIXED_PROTOCOL_AS_OF_REVIEW',review_run_id=assessment_run_id,review_artifact_sha256=sha,as_of_utc=a['at_utc'],
    current_event_count=current_event_count,review_event_count=a['forward_runtime']['network_audit_events'],same_recorded_event_count=a['forward_runtime']['network_audit_events']==current_event_count,current_full_statistics_claimed=False,
    accuracy=a.get('accuracy'),accuracy_lower=a.get('accuracy_lower'),qualified_formal_events=a['qualified_events'],scheduled_denominator=a['scheduled_events'],
    statistical_gate_passed=a['statistical_gate_passed'],economic_evidence=a.get('raw_economic_results'),criteria=a['criteria'])
  except Exception as exc:
   performance.update(status='REQUESTED_REVIEW_UNAVAILABLE',review_run_id=assessment_run_id,error=type(exc).__name__+': '+str(exc),current_full_statistics_claimed=False)
   return performance
 return performance


def snapshot(online=True,assessment_run_id=None):
 now=datetime.now(timezone.utc);p,package,files=legacy.bundle.guard();rp,model,_,rfiles=r2.guard()
 le=legacy.read_records(ROOT,'events');ls=legacy.read_records(ROOT,'scores');legacy.shadow.audit_records(p,le,ls,now)
 r2.run('local');re=r2.records('events');rs=r2.records('scores')
 selected=[max((e for e in le if e['horizon_hours']==h),key=lambda e:f.utc(e['issued_at_utc']),default=None) for h in (1,24)]
 original=[e for e in selected if e];oscores=[s for s in ls if s['event_id'] in {e['event_id'] for e in original}]
 proof,rejected=original_proofs(p,package,files,original,oscores,now,online)
 formal=[e for e in re if e['cohort']=='formal'];latest=max(formal,key=lambda e:f.utc(e['issued_at_utc']),default=None);recent=[latest] if latest else []
 recent_scores=[s for s in rs if s['event_id'] in {e['event_id'] for e in recent}]
 rproof,rrejected=r2_proofs(rp,rfiles,recent,recent_scores,now,online)
 smap={s['event_id']:s for s in ls};rmap={s['event_id']:s for s in rs}
 rows=[forecast_row('original18',h,e,smap.get(e['event_id']) if e else None,*(proof.get(e['event_id'],(False,False)) if e else (False,False)),now) for h,e in zip((1,24),selected)]
 rows.append(forecast_row('r2',1,latest,rmap.get(latest['event_id']) if latest else None,*(rproof.get(latest['event_id'],(False,False)) if latest else (False,False)),now))
 source=operations.source_state((ROOT/r2.SOURCE).read_bytes(),now)
 risks=['影子预测，不授权交易或替换生产模型。','概率校准尚未通过真实前瞻验证。','工程canary不计入正式成绩；缺成本数据不按零收益处理。','近期事件结果不等于完整队列准确率；全样本成绩须读取独立完整评审。','GitHub调度可能错过评分窗口；公开归档不等于锁定留存。']
 if not source['latest73_ready']:risks.append('最新73根输入尚未齐全，当前来源存在时效风险。')
 performance=assessment_panel(assessment_run_id,online,len(re))
 if assessment_run_id is not None:risks.append('独立评审仅代表通过重放的原评审时点；旧评审不适用于当前账本或远端不可达时，隔离该成绩，不影响独立核实的最新预测。')
 result={'schema':'DOGE_PLUS_SHADOW_V1','status':'SHADOW_REPORT_READY','generated_at_utc':now.isoformat(),'generated_at_japan':now.astimezone(JAPAN).isoformat(),
  'source':source,'forecasts':rows,'performance':performance,'risks':risks,'publication_errors':{'original18':rejected,'r2':rrejected},
  'journal_counts':{'original18_events':len(le),'original18_local_scores':len(ls),'r2_formal_events':len(formal),'r2_local_formal_scores':sum(s['event_id'] in {e['event_id'] for e in formal} for s in rs),'r2_engineering_canaries':sum(e['cohort']=='canary' for e in re)},
  'runtime_gaps':{'original18':legacy.plan(p,le,smap,now),'r2':r2.plan(rp,re,rs,now)},
  'models':{'original18_package_sha256':legacy.shadow.digest(package),'r2_identity':model['trial_model_identity']},
  'release_selection':selection_state(online,now),'online_publication_scope':'latest original18 event per horizon and latest R2 formal event; not all history',
  'source_path':r2.SOURCE,'source_sha256':f.old.sha((ROOT/r2.SOURCE).read_bytes()),'source_commit':subprocess.check_output(['git','-c','safe.directory=*','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
  'issuance_performed':False,'production_model_changed':False,'promotion_authorized':False,'real_orders_authorized':False,'external_immutable_retention_verified':False,'validated_70pct':False}
 return result

def render(report):
 esc=lambda x:html.escape(str(x),quote=True)
 labels={'WAIT_ACTIVATION':'尚未生效','NO_FORMAL_ISSUANCE':'尚未发行','UNVERIFIED_PUBLICATION':'公开证据未确认','PENDING_TARGET':'等待真实目标','TARGET_WINDOW_OPEN':'评分窗口已开启','MISSING_TARGET':'逾期缺少目标','TARGET_EVIDENCE_PENDING':'评分留证待确认','TARGET_EVIDENCE_MISSING':'目标留证缺失','SCORED_PUBLICLY_VERIFIED':'已评分并通过公开审计'}
 cards=[]
 for row in report.get('forecasts',[]):
  title=('历史记录 · ' if row.get('historical_record') else '')+('冻结参考' if row['track']=='original18' else 'R2研究挑战者')+' · '+str(row['horizon_hours'])+'小时'
  direction={'UP':'上涨','DOWN':'下跌'}.get(row['direction'],'暂无方向')
  prob='尚未发行或验证' if row['probability_up'] is None else f"上涨模型概率 {row['probability_up']:.1%}（校准未验证）"
  s=row['latest_same_event'];cost='成本净收益未知' if s['net_return'] is None else f"最新同事件名义净收益 {s['net_return']:.2%}"
  stress=esc('未知' if s['stress_net_return'] is None else format(s['stress_net_return'],'.2%'));target=esc(row.get('target_utc','尚未发行'))
  cards.append(f'<section><h2>{esc(title)}</h2><h3>{esc(direction)}</h3><p>{esc(prob)}</p><p class="status">{esc(labels.get(row["status"],row["status"]))}</p><p>{esc(cost)}</p><p>压力净收益：{stress}</p><p>目标（UTC）：{target}</p><small>仅最新事件；不代表全样本准确率。历史记录不会作为新的预测发行。</small></section>')
 perf=report.get('performance',{}); summary='<h2>完整独立成绩</h2><p>未提供当前独立完整评审；准确率与收益未知。</p>'
 if perf.get('status')=='REQUESTED_REVIEW_UNAVAILABLE':summary='<h2>完整独立成绩</h2><p>指定评审当前无法通过完整重放；成绩未知。最新预测仍须独立核实。</p>'
 if perf.get('as_of_utc'):
  summary='<h2>完整独立成绩（原评审时点）</h2><p>评审截止：'+esc(perf['as_of_utc'])+'；正式合格样本：'+esc(perf.get('qualified_formal_events'))+'；准确率：'+esc('未知' if perf.get('accuracy') is None else format(perf['accuracy'],'.2%'))+'。仅代表该时点评审，70%尚未证明。</p>'
 raw=esc(json.dumps(report,ensure_ascii=False,indent=2))
 return '<!doctype html><html lang="zh"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>DOGE PLUS · 影子预测</title><style>body{font:16px/1.6 system-ui;background:#eef2f6;color:#172334;margin:0}main{max-width:1100px;margin:40px auto;padding:24px}h1{font-size:32px}h3{font-size:28px;margin:12px 0}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:18px}section{background:white;padding:24px;border-radius:16px}h2{font-size:18px}.status{color:#445b75}small{color:#637283}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:white;padding:20px;border-radius:12px}li{margin:8px 0}</style><main><h1>DOGE PLUS · 影子预测</h1><p>只读展示真实注册记录，不发行新预测、不下单。晋升关闭，70%尚未证明。</p><p>报告时间：'+esc(report.get('generated_at_japan',report.get('generated_at_utc')))+'<br>小时数据闭合截止（UTC）：'+esc(report.get('source',{}).get('latest_closed_utc','未确认'))+'</p><p>'+esc(report['status'])+'</p><div class="cards">'+''.join(cards)+'</div>'+summary+'<h2>风险与证据边界</h2><ul>'+''.join('<li>'+esc(x)+'</li>' for x in report.get('risks',[]))+'</ul><details><summary>完整模型、事件、基准与成绩审计数据</summary><pre>'+raw+'</pre></details></main></html>'

def main():
 parser=argparse.ArgumentParser();parser.add_argument('--offline',action='store_true');a=parser.parse_args();out=ROOT/'plus-shadow-output';out.mkdir(exist_ok=True)
 if out.is_symlink() or any(p.is_symlink() for p in out.iterdir()) or any(p.name not in ('report.json','index.html','failure.json','.owner') for p in out.iterdir()):raise ValueError('foreign PLUS output cannot be overwritten')
 marker=out/'.owner'
 if any(out.iterdir()) and (not marker.is_file() or marker.read_bytes()!=b'DOGE_PLUS_SHADOW_V1\n'):raise ValueError('PLUS output owner missing')
 marker.write_bytes(b'DOGE_PLUS_SHADOW_V1\n')
 try:
  rid=os.environ.get('PLUS_ASSESSMENT_RUN_ID','')
  if rid and (not rid.isdigit() or int(rid)<=0):raise ValueError('positive actual review run ID required')
  report=snapshot(not a.offline,int(rid) if rid else None)
 except Exception as exc:
  report={'schema':'DOGE_PLUS_SHADOW_V1','status':'BLOCKED_INTEGRITY_OR_REMOTE_EVIDENCE','generated_at_utc':datetime.now(timezone.utc).isoformat(),'forecasts':[],'source':{},'performance':{},'risks':['无法核实来源或证据，停止展示方向与概率。'],
   'error':type(exc).__name__+': '+str(exc),'issuance_performed':False,'production_model_changed':False,'real_orders_authorized':False,'promotion_authorized':False,'validated_70pct':False}
 (out/'report.json').write_bytes(f.encode(report));(out/'index.html').write_text(render(report),encoding='utf-8')
 print(json.dumps({k:report[k] for k in ('status','issuance_performed','production_model_changed','promotion_authorized','real_orders_authorized')}))
 if report['status'].startswith('BLOCKED'):raise SystemExit(1)

if __name__=='__main__':main()
