"""Read-only evidence and presentation boundary for the eleven-chapter report."""
import json,math
from datetime import datetime,date,time,timedelta,timezone

def evidence_state(direction,quality,cutoff,now):
 direction_ok=False
 try:
  d=datetime.fromisoformat(direction['sources']['daily_last_closed_start_utc'].replace('Z','+00:00'))
  direction_ok=direction.get('status')=='ok' and d==datetime.combine(date.fromisoformat(cutoff),time(),timezone.utc)
 except (KeyError,ValueError,TypeError,AttributeError):pass
 fresh=False
 try:
  q=datetime.fromisoformat(quality['audited_at_utc'].replace('Z','+00:00'))
  fresh=q.tzinfo is not None and 0<=(now-q).total_seconds()<=7200
 except (KeyError,ValueError,TypeError,AttributeError):pass
 state=quality.get('status','unavailable') if fresh else 'STALE_OR_INVALID'
 healthy=state in ('ok','ok_with_coverage_limits') and not quality.get('critical_failures')
 return {'overall':'RESEARCH_ONLY_NO_VALIDATED_EDGE' if direction_ok and healthy else 'DEGRADED_NO_VALIDATED_EDGE',
  'direction_available':direction_ok,'quality_current_status':state,'quality_audited_at_utc':quality.get('audited_at_utc'),
  'prediction_edge_validated':False,'quality_max_age_hours':2,'critical_failures':quality.get('critical_failures',[])}

def daily_target_close(day):
 return datetime.combine(date.fromisoformat(day)+timedelta(days=1),time(),timezone.utc).isoformat()

def current_plus(online=False):
 # Never consume a previously rendered JSON/HTML snapshot as current evidence.
 try:
  import plus_shadow
  return plus_shadow.snapshot(online=online)
 except Exception as exc:
  return {'status':'UNAVAILABLE','forecasts':[],'error':type(exc).__name__+': '+str(exc),
   'validated_70pct':False,'promotion_authorized':False,'real_orders_authorized':False}

def cell(value):
 if value is None:return '未知'
 return str(value).replace('|','\\|').replace('\n',' ').replace('\r',' ')

def pct(value):
 return format(value,'.2%') if type(value) in (int,float) and math.isfinite(value) else '未知'

def plus_sections(report):
 f1=['### DOGE PLUS｜冻结轨道（只读）',
  '上涨概率未经真实前瞻校准；DOWN＝跌／平，目标价等于参考价时 y=0。未核实发行不显示方向或概率；目标证据仅决定是否可评分。',
  '|轨道/周期|事件|状态 / 生效UTC|参考报价UTC / 参考价USDT|发行UTC|目标UTC|报价至发行延迟秒|方向|上涨模型概率|',
  '|---|---|---|---|---|---|---:|---|---:|']
 f10=['### PLUS 独立前瞻与同事件对照',
  '旧系统前瞻、历史回测、original18、R2及canary分别列示，禁止合并准确率。下表仅为最新事件，不能代表完整队列成绩。',
  '|轨道/周期|事件|合格最新评分数|命中(0/1)|Brier|成本净收益|压力净收益|同事件冻结基准|',
  '|---|---|---:|---:|---:|---:|---:|---|']
 if report.get('status')!='SHADOW_REPORT_READY':
  return {'F01':'\n'.join(f1+['PLUS 当前证据不可用，方向和概率均不展示。']),
   'F10':'\n'.join(f10+['PLUS 成绩未知，不沿用旧快照。']),
   'F11':'PLUS 当前核实失败：'+cell(report.get('error',report.get('status')))+'；晋升关闭。'}
 seen=set()
 for r in report.get('forecasts',[]):
  key=(r.get('track'),r.get('horizon_hours'))
  if key not in (('original18',1),('original18',24),('r2',1)) or key in seen:continue
  seen.add(key);name=key[0]+'/'+str(key[1])+'h'
  verified=r.get('prediction_verified') is True and r.get('status') not in ('WAIT_ACTIVATION','NO_FORMAL_ISSUANCE','UNVERIFIED_PUBLICATION')
  direction={'UP':'上涨','DOWN':'跌／平'}.get(r.get('direction'),'未知') if verified else '未知'
  probability=pct(r.get('probability_up')) if verified else '未知'
  f1.append('|'+ '|'.join(map(cell,[name,r.get('event_id'),str(r.get('status'))+' / '+str(r.get('effective_utc','未知')),str(r.get('reference_quote_utc','未知'))+' / '+str(r.get('reference_price','未知')),r.get('issued_at_utc'),r.get('target_utc'),r.get('reference_to_issue_seconds'),direction,probability]))+'|')
  s=r.get('latest_same_event',{}) if verified and r.get('score_verified') is True else {}
  f10.append('|'+ '|'.join(map(cell,[name,r.get('event_id'),s.get('sample_count',0),s.get('accuracy'),s.get('brier'),pct(s.get('net_return')),pct(s.get('stress_net_return')),json.dumps(s.get('baselines',{}),ensure_ascii=False,sort_keys=True)]))+'|')
 perf=report.get('performance',{})
 f10+=['完整独立评审：'+cell(perf.get('status'))+'；评审截止 '+cell(perf.get('as_of_utc'))+'；正式合格样本 '+cell(perf.get('qualified_formal_events'))+'；准确率 '+pct(perf.get('accuracy'))+'。未提供适用评审时保持未知；70%及经济优势尚未证明。',
  '分轨账本记录数（不是合格评分数）：'+cell(json.dumps(report.get('journal_counts',{}),ensure_ascii=False,sort_keys=True)),
  '计划槽位、漏发、漏评：'+cell(json.dumps(report.get('runtime_gaps',{}),ensure_ascii=False,sort_keys=True))]
 f11=['PLUS 本次核实生成 '+cell(report.get('generated_at_utc'))+'；小时闭合截止 '+cell(report.get('source',{}).get('latest_closed_utc'))+'；来源提交 '+cell(report.get('source_commit'))+'；来源哈希 '+cell(report.get('source_sha256')),
  '冻结身份：'+cell(json.dumps(report.get('models',{}),sort_keys=True)),'发布证据错误：'+cell(json.dumps(report.get('publication_errors',{}),ensure_ascii=False,sort_keys=True)),
  '晋升关闭；预测优势、校准、长期成本净收益尚未证明。未配置独立锁定存储，GitHub留存存在期限；不以独立管理方为必备门槛。']
 f11+=['- '+cell(risk) for risk in report.get('risks',[])]
 return {'F01':'\n'.join(f1),'F10':'\n'.join(f10),'F11':'\n'.join(f11)}
