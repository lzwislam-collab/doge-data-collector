"""Read-only complete cohorts; frozen final slot deadlines, no new estimator."""
from datetime import datetime,timedelta,timezone

def utc(value):
 at=datetime.fromisoformat(value.replace('Z','+00:00'))
 if at.tzinfo is None or at.utcoffset()!=timedelta(0):raise ValueError('UTC timestamp required')
 return at.astimezone(timezone.utc)

def indexed(rows):
 result={}
 for row in rows:
  if row['event_id'] in result:raise ValueError('duplicate event/score')
  result[row['event_id']]=row
 return result

def cohort(track,h,p,events,scores,proofs,now,row_builder):
 if (track,h) not in (('original18',1),('original18',24),('r2',1)):raise ValueError('unregistered cohort')
 emap=indexed(events);smap=indexed(scores)
 if set(smap)-set(emap):raise ValueError('orphan score')
 start=utc(p['effective_from_utc']);end=utc(p['observation_end_utc']) if track=='r2' else None
 step=p['slot_seconds'] if track=='r2' else p['slot_steps_hours'][str(h)]*3600
 delay=p['issue_window_seconds'] if track=='r2' else p['max_issue_delay_minutes']*60
 window=p['target_window_seconds'] if track=='r2' else p['target_window_minutes']*60
 for key,e in emap.items():
  slot=utc(e['slot_utc']);issued=utc(e['issued_at_utc'])
  expected=str(int(slot.timestamp()))+('_formal' if track=='r2' else '_'+str(h))
  if key!=expected or slot<start or (slot-start).total_seconds()%step or end and slot>=end:raise ValueError('off-grid event')
  if not slot<=issued<slot+timedelta(seconds=delay) or issued>now:raise ValueError('issuance timestamp outside frozen slot')
 rows=[];slot=start;hits=denom=valid=missing_issue=missing_eval=unverified_issue=0
 while slot<=now and (end is None or slot<end):
  key=str(int(slot.timestamp()))+('_formal' if track=='r2' else '_'+str(h));e=emap.get(key);s=smap.get(key)
  due=slot+timedelta(seconds=delay+h*3600+window)<=now
  pred,scored=proofs.get(key,(False,False))
  if scored and not pred:raise ValueError('score without verified issuance')
  row=row_builder(track,h,e,s,pred,scored,now)
  row.update(planned_event_id=key,slot_utc=slot.isoformat(),in_fixed_denominator=due,
   actual_direction=('UP' if s['y'] else 'DOWN') if s is not None and pred and scored else None,local_score_present=s is not None)
  if e is None:row['status']='MISSING_ISSUANCE' if slot+timedelta(seconds=delay)<=now else 'WAIT_ISSUE_WINDOW'
  if due:
   denom+=1
   if e is None:missing_issue+=1
   elif not pred:unverified_issue+=1
   elif not row['score_verified']:missing_eval+=1
   else:valid+=1;hits+=row['latest_same_event']['accuracy']
  rows.append(row);slot+=timedelta(seconds=step)
 return {'track':track,'horizon_hours':h,'fixed_denominator':denom,'hits':hits,'score_label':str(hits)+'/'+str(denom),
  'accuracy_fixed':hits/denom if denom else None,'qualified_scored':valid,'coverage':valid/denom if denom else None,
  'missing_issuance':missing_issue,'unverified_issuance':unverified_issue,'missing_evaluation':missing_eval,
  'duplicate_count':0,'timestamp_errors':0,'rows':rows,
  'scope':'original18 full candidate only' if track=='original18' else 'R2 selected candidate only',
  'denominator_rule':'all planned slots after final possible target deadline; missing/unverified earns no hit; pending excluded',
  'statistical_promotion_authorized':False,'calibration_verified':False}

def summary_lines(card):
 lines=['### PLUS 完整固定分母成绩（分轨）','未到最终评分期限的槽位不入分母；漏发、漏评和未核实记录保留并不得计为命中。0/0表示尚无成熟样本，非0%准确率。',
  '|轨道/周期|命中/固定分母|已核实评分|覆盖率|漏发|发行未核实|漏评|','|---|---:|---:|---:|---:|---:|---:|']
 for c in card.get('cohorts',[]):
  coverage='未知' if c['coverage'] is None else format(c['coverage'],'.1%')
  lines.append('|'+ '|'.join(map(str,[c['track']+'/'+str(c['horizon_hours'])+'h',c['score_label'],c['qualified_scored'],coverage,c['missing_issuance'],c['unverified_issuance'],c['missing_evaluation']]))+'|')
 return lines
