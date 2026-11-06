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

def _history_stamp(root,relative,columns,current):
 """Per-call input identity only; never store or reuse a validation result."""
 root=Path(root).resolve();gd=Path(git(root,'rev-parse','--absolute-git-dir'))
 common=Path(git(root,'rev-parse','--path-format=absolute','--git-common-dir'))
 files=[root/relative,root/PROTOCOL,root/'data/stage5/forward_validation_protocol_v2.json',
        gd/'HEAD',gd/'FETCH_HEAD',gd/'config',common/'config',common/'shallow',common/'info/grafts',Path(__file__)]
 def identity(p):
  if p.is_symlink():raise ValueError('redirected history input')
  return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None
 return (str(root),str(gd.resolve()),str(common.resolve()),root.stat().st_dev,root.stat().st_ino,
         git(root,'rev-parse','HEAD'),git(root,'for-each-ref','--format=%(refname) %(objectname)'),
         tuple((str(p),identity(p)) for p in files),tuple(columns),json.dumps(current,sort_keys=True,ensure_ascii=False),
         tuple(sorted((k,v) for k,v in os.environ.items() if k.startswith('GIT_'))))

def _history_windows_job(process):
 """Own only this reader and its children; closing the handle kills the tree."""
 import ctypes
 from ctypes import wintypes as w
 class Basic(ctypes.Structure):
  _fields_=[('process_time',ctypes.c_longlong),('job_time',ctypes.c_longlong),('flags',w.DWORD),
   ('min_ws',ctypes.c_size_t),('max_ws',ctypes.c_size_t),('active',w.DWORD),('affinity',ctypes.c_size_t),('priority',w.DWORD),('scheduling',w.DWORD)]
 class IO(ctypes.Structure):_fields_=[(n,ctypes.c_ulonglong) for n in ('read_ops','write_ops','other_ops','read_bytes','write_bytes','other_bytes')]
 class Extended(ctypes.Structure):
  _fields_=[('basic',Basic),('io',IO),('process_mem',ctypes.c_size_t),('job_mem',ctypes.c_size_t),('peak_process',ctypes.c_size_t),('peak_job',ctypes.c_size_t)]
 kernel=ctypes.WinDLL('kernel32',use_last_error=True)
 kernel.CreateJobObjectW.argtypes=[ctypes.c_void_p,w.LPCWSTR];kernel.CreateJobObjectW.restype=w.HANDLE
 kernel.SetInformationJobObject.argtypes=[w.HANDLE,ctypes.c_int,ctypes.c_void_p,w.DWORD]
 kernel.AssignProcessToJobObject.argtypes=[w.HANDLE,w.HANDLE]
 kernel.CloseHandle.argtypes=[w.HANDLE]
 job=kernel.CreateJobObjectW(None,None)
 if not job:raise ctypes.WinError(ctypes.get_last_error())
 info=Extended();info.basic.flags=0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
 if not kernel.SetInformationJobObject(job,9,ctypes.byref(info),ctypes.sizeof(info)) or not kernel.AssignProcessToJobObject(job,w.HANDLE(int(process._handle))):
  error=ctypes.get_last_error();kernel.CloseHandle(job);raise ctypes.WinError(error)
 # Popen closed the primary thread handle. Find that still-suspended thread,
 # bind the Job first, and only then allow any Git/user child code to execute.
 class Thread(ctypes.Structure):
  _fields_=[('size',w.DWORD),('usage',w.DWORD),('tid',w.DWORD),('pid',w.DWORD),('base',w.LONG),('delta',w.LONG),('flags',w.DWORD)]
 kernel.CreateToolhelp32Snapshot.argtypes=[w.DWORD,w.DWORD];kernel.CreateToolhelp32Snapshot.restype=w.HANDLE
 kernel.Thread32First.argtypes=[w.HANDLE,ctypes.POINTER(Thread)];kernel.Thread32Next.argtypes=[w.HANDLE,ctypes.POINTER(Thread)]
 kernel.OpenThread.argtypes=[w.DWORD,w.BOOL,w.DWORD];kernel.OpenThread.restype=w.HANDLE
 kernel.ResumeThread.argtypes=[w.HANDLE];kernel.ResumeThread.restype=w.DWORD
 snapshot=kernel.CreateToolhelp32Snapshot(4,0);handle=None
 try:
  if snapshot==w.HANDLE(-1).value:raise ctypes.WinError(ctypes.get_last_error())
  entry=Thread();entry.size=ctypes.sizeof(entry);tids=[]
  more=kernel.Thread32First(snapshot,ctypes.byref(entry))
  while more:
   if entry.pid==process.pid:tids.append(entry.tid)
   more=kernel.Thread32Next(snapshot,ctypes.byref(entry))
  if len(tids)!=1:raise RuntimeError('suspended history process thread identity is ambiguous')
  handle=kernel.OpenThread(2,False,tids[0])  # THREAD_SUSPEND_RESUME
  if not handle:raise ctypes.WinError(ctypes.get_last_error())
  if kernel.ResumeThread(handle)!=1:raise RuntimeError('history process was not safely suspended')
 except BaseException:
  kernel.CloseHandle(job);raise
 finally:
  if handle:kernel.CloseHandle(handle)
  if snapshot and snapshot!=w.HANDLE(-1).value:kernel.CloseHandle(snapshot)
 return kernel,job

class HistoryReadError(RuntimeError):
 """A local history I/O failure, never a transient market-network failure."""

def _history_no_replace_is_set():
 """Match the environment inherited by Git, including a present empty value."""
 if os.name!='nt':return 'GIT_NO_REPLACE_OBJECTS' in os.environ
 # On Windows os.environ may retain '' after SetEnvironmentVariable deleted
 # the native entry. Query that native environment without changing it.
 import ctypes
 from ctypes import wintypes
 kernel=ctypes.WinDLL('kernel32',use_last_error=True)
 kernel.GetEnvironmentVariableW.argtypes=[wintypes.LPCWSTR,wintypes.LPWSTR,wintypes.DWORD]
 kernel.GetEnvironmentVariableW.restype=wintypes.DWORD
 ctypes.set_last_error(0)
 size=kernel.GetEnvironmentVariableW('GIT_NO_REPLACE_OBJECTS',None,0)
 error=ctypes.get_last_error()
 if size:return True
 if error==203:return False  # ERROR_ENVVAR_NOT_FOUND
 if error==0:return True  # Present but empty; Git must still disable replaces.
 raise ctypes.WinError(error)

class _HistoryBatch:
 """One outstanding query, binary framing, bounded inactivity, no PASS cache."""
 def __init__(self,root,timeout=30,command=None):
  self.root=root;self.timeout=timeout;self.command=command or ['git','cat-file','--batch-command','-Z']
 def __enter__(self):
  import queue,threading,tempfile
  self.queue=queue.Queue(maxsize=4);self.writes=queue.Queue(maxsize=1)
  self.stop=threading.Event();self.buffer=bytearray();self.job=None;self.threads=[]
  self.stderr=tempfile.TemporaryFile()
  try:
   self.replacements={}
   try:replace_config=git(self.root,'config','--bool','--get','core.useReplaceRefs')
   except subprocess.CalledProcessError as exc:
    if exc.returncode!=1:raise
    replace_config='true'
   if not _history_no_replace_is_set() and replace_config!='false':
    base=os.environ.get('GIT_REPLACE_REF_BASE','refs/replace')
    for line in git(self.root,'for-each-ref','--format=%(refname) %(objectname)',base).splitlines():
     ref,oid=line.split();self.replacements[ref.rsplit('/',1)[1]]=oid
   self.process=subprocess.Popen(self.command,cwd=self.root,stdin=subprocess.PIPE,stdout=subprocess.PIPE,
      stderr=self.stderr,creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP|0x4|0x08000000) if os.name=='nt' else 0,start_new_session=os.name!='nt')
   if os.name=='nt':self.job=_history_windows_job(self.process)
   self.threads=[threading.Thread(target=self._reader,daemon=True),threading.Thread(target=self._writer,daemon=True)]
   for thread in self.threads:thread.start()
   return self
  except BaseException:
   self._close(True);raise
 def _reader(self):
  import queue
  try:
   while not self.stop.is_set():
    block=self.process.stdout.read1(65536)
    while not self.stop.is_set():
     try:self.queue.put(block,timeout=.05);break
     except queue.Full:pass
    if not block:break
  except Exception as exc:
   while not self.stop.is_set():
    try:self.queue.put(exc,timeout=.05);break
    except queue.Full:pass
 def _writer(self):
  import queue
  while not self.stop.is_set():
   try:item=self.writes.get(timeout=.05)
   except queue.Empty:continue
   data,done,errors=item
   try:
    if data is None:self.process.stdin.close()
    else:self.process.stdin.write(data);self.process.stdin.flush()
   except Exception as exc:errors.append(exc)
   finally:done.set()
   if data is None:break
 def _send(self,data):
  import threading,queue
  done=threading.Event();errors=[]
  try:self.writes.put((data,done,errors),timeout=self.timeout)
  except queue.Full:raise TimeoutError('history Git writer stalled')
  if not done.wait(self.timeout):raise TimeoutError('history Git writer stalled')
  if errors:raise errors[0]
 def _more(self):
  import queue
  try:block=self.queue.get(timeout=self.timeout)
  except queue.Empty:raise TimeoutError('history Git response stalled')
  if isinstance(block,Exception):raise block
  if not block:
   code=self.process.wait(timeout=self.timeout)
   if code:
    self.stderr.seek(0)
    raise subprocess.CalledProcessError(code,self.command,stderr=self.stderr.read(65536).decode('utf-8',errors='replace'))
   raise ValueError('truncated history Git response')
  self.buffer.extend(block)
 def _exact(self,n):
  # Buffer one object at a time, never all historical bodies.
  while len(self.buffer)<n:self._more()
  result=bytes(self.buffer[:n]);del self.buffer[:n];return result
 def _header(self):
  while b'\0' not in self.buffer:
   if len(self.buffer)>65536:raise ValueError('oversized history Git header')
   self._more()
  end=self.buffer.index(0);line=self._exact(end+1)[:-1]
  parts=line.split(b' ')
  if len(parts)!=3:
   raise subprocess.CalledProcessError(128,self.command,output=line)
  oid,kind,size=parts
  if len(oid) not in (40,64) or any(c not in b'0123456789abcdef' for c in oid) or kind!=b'blob' or not size.isdigit():
   raise ValueError('invalid history Git object identity/type/length')
  return oid,kind,int(size)
 def read_blob(self,expression):
  if '\0' in expression:raise ValueError('NUL in history object request')
  self._send(b'info '+expression.encode('utf-8')+b'\0');expected=self._header()
  self._send(b'contents '+expected[0]+b'\0');actual=self._header()
  if actual!=expected:raise ValueError('history Git response does not match request')
  raw=self._exact(actual[2])
  if self._exact(1)!=b'\0':raise ValueError('invalid history Git object separator')
  identity=actual[0].decode('ascii');seen=set()
  while identity in self.replacements:
   if identity in seen:raise ValueError('cyclic Git replacement identity')
   seen.add(identity);identity=self.replacements[identity]
  digest=hashlib.sha1 if len(identity)==40 else hashlib.sha256
  if digest(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()!=identity:
   raise ValueError('history Git object content identity mismatch')
  return io.TextIOWrapper(io.BytesIO(raw),encoding='utf-8',errors='strict',newline=None).read().strip()
 def threads_alive(self):return any(t.is_alive() for t in self.threads)
 def _kill_tree(self):
  if self.job:
   kernel,job=self.job;kernel.CloseHandle(job);self.job=None
  elif hasattr(self,'process'):
   if os.name!='nt':
    import signal
    try:os.killpg(self.process.pid,signal.SIGKILL)
    except ProcessLookupError:pass
   elif self.process.poll() is None:self.process.kill()
 def _close(self,failed):
  import queue
  error=None
  if not hasattr(self,'process'):
   self.stderr.close();return
  try:
   if failed:self._kill_tree()
   else:
    self._send(None)
    self.process.wait(timeout=self.timeout)
    if self.process.returncode:
     self.stderr.seek(0)
     raise subprocess.CalledProcessError(self.process.returncode,self.command,stderr=self.stderr.read(65536).decode('utf-8',errors='replace'))
    # Let the reader deliver EOF (or unsolicited bytes) before setting stop.
    for thread in self.threads:thread.join(timeout=min(2,self.timeout))
    if self.threads_alive():raise ValueError('undrained history Git output')
   self._kill_tree()
  except BaseException as exc:
   error=exc;self._kill_tree()
  finally:
   self.stop.set()
   try:self.process.wait(timeout=5)
   except subprocess.TimeoutExpired:self.process.kill();self.process.wait(timeout=5)
   for thread in self.threads:thread.join(timeout=2)
   if self.threads_alive():error=RuntimeError('history Git reader threads did not stop')
   if not failed and not error:
    extras=bool(self.buffer)
    while True:
     try:item=self.queue.get_nowait();extras=extras or bool(item)
     except queue.Empty:break
    if extras:error=ValueError('unexpected trailing history Git output')
   for stream in (self.process.stdin,self.process.stdout,self.stderr):
    if not stream.closed:stream.close()
  if self.threads_alive():raise RuntimeError('history Git cleanup incomplete')
  if error and not failed:raise error
 def __exit__(self,kind,value,tb):self._close(kind is not None);return False

def history_receipts(root,relative,columns,current):
 """Keep local Git errors out of transport retry, preserving their causes."""
 try:return _history_receipts(root,relative,columns,current)
 except (OSError,subprocess.SubprocessError) as exc:
  raise HistoryReadError('local Git history I/O failed: '+type(exc).__name__) from exc

def _history_receipts(root,relative,columns,current):
 """Check every reachable revision, including deletions. Retain first commit receipt."""
 if git(root,'rev-parse','--is-shallow-repository')!='false':
  raise ValueError('full git history required')
 stamp=_history_stamp(root,relative,columns,current)
 revisions=[]
 # Resolve the same initial history even if HEAD moves away and back mid-call.
 for line in git(root,'log','--reverse','--format=%H%x00%cI',stamp[5],'--',relative).splitlines():
  parts=line.split('\0')
  if len(parts)!=2:raise ValueError('invalid history revision metadata')
  revisions.append(parts)
 prior={};receipts={}
 with _HistoryBatch(root) as batch:
  for rev,committed in revisions:
   body=batch.read_blob(rev+':'+relative)
   reader=csv.DictReader(io.StringIO(body))
   if reader.fieldnames!=columns:raise ValueError('historic schema revised: '+relative)
   snap={}
   for row in reader:
    fid=row['forecast_id']
    if not fid or fid in snap:raise ValueError('duplicate historical ID')
    snap[fid]=row
   if any(snap.get(k)!=v for k,v in prior.items()):
    raise ValueError('committed record revised: '+relative)
   for fid in snap.keys()-prior.keys():
    receipts[fid]={'commit_sha':rev,'committed_at_utc':datetime.fromisoformat(committed).astimezone(timezone.utc).isoformat()}
   prior=snap
 if any(current.get(k)!=v for k,v in prior.items()):
  raise ValueError('current record revised: '+relative)
 if _history_stamp(root,relative,columns,current)!=stamp:raise ValueError('history verification input changed during read')
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
