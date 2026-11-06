"""Independent, bounded TARGET-only capture. No issuance, backfill, or orders."""
import argparse,hashlib,json,os,re,subprocess,sys,tempfile,time
from contextlib import contextmanager
from datetime import datetime,timedelta,timezone
from pathlib import Path
import direction_forward_v42 as frozen
import forward_validation as base
import forward_validation_v2 as v2
import quote_evidence_v2 as evidence
import quote_capture_retry as retry
import publish_forward_evidence as publisher

RESERVED_MINUTES=5

class RescueFailure(ValueError):
    def __init__(self,message,evidence):
        super().__init__(message);self.evidence=evidence

def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def business_identity(root):
    paths=set(publisher.PATHS)|{'data/stage5/spot_plus/quote_watch_latest.json'}
    return {'state':take_state(root),'files':{name:digest(root/name) if (root/name).exists() else None for name in paths}}

def local_git(root,*args):
    return subprocess.run(['git','--no-optional-locks',*args],cwd=root,
                          check=True,capture_output=True,timeout=30).stdout

@contextmanager
def private_git_environment(home):
    # This entry is single-writer under the existing workflow lock. No frozen
    # function is replaced; its Git subprocesses inherit this process context.
    check_git_environment()
    empty=home/'empty.gitconfig';empty.write_bytes(b'')
    values={'GIT_CONFIG_NOSYSTEM':'1','GIT_CONFIG_GLOBAL':str(empty),
            'GIT_OPTIONAL_LOCKS':'0','GIT_TERMINAL_PROMPT':'0'}
    old={key:os.environ.get(key) for key in values}
    os.environ.update(values)
    try:yield
    finally:
        for key,value in old.items():
            if value is None:os.environ.pop(key,None)
            else:os.environ[key]=value

def check_git_environment():
    bad=[key for key in os.environ if key.startswith('GIT_') and key!='GIT_PAGER']
    if bad:raise ValueError('redirecting Git environment rejected: '+','.join(sorted(bad)))

def safe_audit(root,payload):
    path=root/'quote_rescue_result.json'
    if path.is_symlink() or (path.exists() and path.stat().st_nlink!=1):
        raise ValueError('audit output is a linked file')
    if path.resolve().parent!=root.resolve():raise ValueError('audit output escapes root')
    encoded=json.dumps(payload,ensure_ascii=False,indent=2)+'\n'
    if len(encoded.encode('utf-8'))>4*1024*1024:raise ValueError('audit budget exceeded; private attempt retained')
    # The result is the existing designated artifact, never a business ledger.
    with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=root,
            prefix='.quote-rescue-audit-',delete=False) as stream:
        stream.write(encoded);temporary=stream.name
    os.replace(temporary,path)

def metadata(root):
    top=Path(local_git(root,'rev-parse','--show-toplevel').decode().strip()).resolve()
    if top!=root.resolve():raise ValueError('Git worktree redirects outside supplied root')
    directory=Path(local_git(root,'rev-parse','--absolute-git-dir').decode().strip())
    common=Path(local_git(root,'rev-parse','--path-format=absolute','--git-common-dir').decode().strip())
    paths={directory/name for name in ('index','HEAD','FETCH_HEAD','ORIG_HEAD','config','config.worktree','packed-refs')}
    paths|={common/name for name in ('HEAD','FETCH_HEAD','config','packed-refs')}
    paths|={p for p in (common/'refs').rglob('*') if p.is_file()}
    index=Path(local_git(root,'rev-parse','--path-format=absolute','--git-path','index').decode().strip()).resolve()
    if index!=directory.resolve()/'index':raise ValueError('redirected Git index')
    return {'worktree':str(top),'index':str(index),'git_dir':str(directory),'common_dir':str(common),
            'files':{str(p):digest(p) for p in paths if p.is_file()}}

def code_identity():
    modules={m.__name__:m for m in (frozen,base,v2,evidence,retry,publisher)}
    for name in v2.REQUIRED_CODE:
        module=sys.modules.get(Path(name).stem)
        if module is not None:modules[module.__name__]=module
    home=Path(__file__).resolve().parent
    out={'entry':{'path':str(Path(__file__).resolve()),'sha256':digest(__file__)}}
    for name,module in modules.items():
        path=Path(module.__file__).resolve()
        if path.parent!=home:raise ValueError('runtime module outside candidate: '+name)
        out[name]={'path':str(path),'sha256':digest(path)}
    return out

def safe_stage(root):
    """Independent Git metadata does not by itself exclude file redirection."""
    for directory,children,files in os.walk(root,followlinks=False):
        for name in children+files:
            path=Path(directory)/name
            if path.is_symlink() or path.resolve()!=path.absolute():
                raise ValueError('linked or redirected attempt path')
            if path.is_file() and path.stat().st_nlink!=1:
                raise ValueError('hardlinked attempt business file')

def private_metadata(root):
    safe_stage(root)
    meta=metadata(root)
    if Path(meta['git_dir']).resolve()!=root.resolve()/'.git' or meta['git_dir']!=meta['common_dir']:
        raise ValueError('attempt Git metadata no longer independent')
    return meta

def attempt_bytes(home):
    try:
        safe_stage(home)
        return sum(p.stat().st_size for p in home.rglob('*') if p.is_file())
    except (ValueError,OSError):return None

def create_attempt(source,home,identity):
    origin=local_git(source,'remote','get-url','origin').decode().strip()
    if origin.startswith(('https:','http:','ssh:','git@')):
        if origin not in ('https://github.com/lzwislam-collab/doge-data-collector','https://github.com/lzwislam-collab/doge-data-collector.git'):
            raise ValueError('unexpected production origin')
    else:
        if not Path(origin).is_absolute():raise ValueError('local test remote must be absolute')
        remote=Path(origin).resolve();source_resolved=source.resolve()
        common=Path(local_git(source,'rev-parse','--path-format=absolute','--git-common-dir').decode().strip()).resolve()
        if (remote==source_resolved or source_resolved in remote.parents or remote in source_resolved.parents
                or remote==common or common in remote.parents or remote in common.parents):
            raise ValueError('local test origin aliases canonical repository')
        if local_git(remote,'rev-parse','--is-bare-repository').strip()!=b'true':
            raise ValueError('local test origin must be an independent bare repository')
    stage=home/'work'
    # Clone the actual remote main, NOT the candidate's HEAD or shared object DB.
    # Full commit history; missing blobs may be obtained by normal Git reads.
    local_git(source,'clone','--no-local','--no-hardlinks','--no-checkout',
              '--filter=blob:none','--single-branch','--branch','main',origin,str(stage))
    for key,value in [('core.autocrlf','false'),('core.longpaths','true'),('core.hooksPath',str(home/'no-hooks'))]:
        local_git(stage,'config','--local',key,value)
    if origin.startswith('https:'):
        # Reuse only the existing GitHub checkout header, scoped to this same
        # fixed host. Never copy arbitrary source config or print credentials.
        header=subprocess.run(['git','--no-optional-locks','config','--local','--get',
            'http.https://github.com/.extraheader'],cwd=source,capture_output=True).stdout.decode().strip()
        if not re.fullmatch(r'(?i)AUTHORIZATION: basic [A-Za-z0-9+/=]+',header):
            raise ValueError('existing scoped checkout credential unavailable')
        with (stage/'.git/config').open('a',encoding='utf-8') as stream:
            stream.write('\n[http "https://github.com/"]\n\textraheader = '+json.dumps(header)+'\n')
    required=set(v2.REQUIRED_CODE)|{base.PROTOCOL,v2.PROTOCOL,base.WITNESSES,base.RUN_CACHE,
        evidence.BINDINGS,'quote_only_rescue.py','quote_capture_retry.py','publish_forward_evidence.py'}
    required.update(publisher.PATHS)
    patterns=['/'+p for p in sorted(required)]+['/'+evidence.BASE+'/snapshots/']
    local_git(stage,'sparse-checkout','set','--no-cone',*patterns)
    local_git(stage,'checkout','main')
    safe_stage(stage)
    meta=metadata(stage)
    if Path(meta['git_dir'])!=stage/'.git' or meta['git_dir']!=meta['common_dir']:
        raise ValueError('attempt Git metadata is not independent')
    if (stage/'.git/objects/info/alternates').exists():raise ValueError('shared object database rejected')
    if local_git(stage,'rev-parse','--is-shallow-repository').strip()!=b'false':raise ValueError('full history required')
    if any(p.stat().st_nlink!=1 for p in (stage/'.git/objects').rglob('*') if p.is_file()):
        raise ValueError('hardlinked Git objects rejected')
    names=set(v2.REQUIRED_CODE)|{'quote_capture_retry.py','publish_forward_evidence.py'}
    for name in names:
        executing=(Path(__file__).resolve().parent/name).read_bytes().replace(b'\r\n',b'\n')
        if ((stage/name).read_bytes().replace(b'\r\n',b'\n')!=executing
                or (source/name).read_bytes().replace(b'\r\n',b'\n')!=executing):
            raise ValueError('production/runtime code drift: '+name)
    disk_code={name:digest(stage/name) for name in names|{'quote_only_rescue.py'}}
    return stage,{'path':str(stage),'base_head':local_git(stage,'rev-parse','HEAD').decode().strip(),
                  'git_identity':meta,'code_identity':identity,'disk_code':disk_code}

def git_rows(root,head,path,columns):
    import csv,io
    text=local_git(root,'show',head+':'+path).decode('utf-8')
    reader=csv.DictReader(io.StringIO(text))
    if reader.fieldnames!=columns:raise ValueError('remote CSV schema mismatch')
    result={}
    for row in reader:
        key=row[columns[0]]
        if not key or key in result or None in row:raise ValueError('remote duplicate or malformed record')
        result[key]=row
    return result

def remote_effect(stage,head,quotes):
    """Business identity, not commit SHA: survives rebases and later commits."""
    scores=git_rows(stage,head,base.SCORES,frozen.SCOLS)
    bindings=git_rows(stage,head,evidence.BINDINGS,evidence.BCOLS)
    registry=git_rows(stage,head,base.REG,frozen.FCOLS)
    present=[]
    for q in quotes:
        fid=q['forecast_id'];binding=bindings.get(fid+'|TARGET');score=scores.get(fid)
        if not binding and not score:present.append(False);continue
        expected_binding={'forecast_id':fid+'|TARGET','prediction_id':fid,'kind':'TARGET','quote_id':q['quote_id']}
        if (binding!=expected_binding or not score or score!=q['score']
                or registry.get(fid)!=q['prediction']):
            raise ValueError('remote TARGET identity conflict')
        relative=evidence.snapshot_path(stage,q['quote_id']).relative_to(stage).as_posix()
        if hashlib.sha256(local_git(stage,'show',head+':'+relative)).hexdigest()!=q['quote_id']:
            raise ValueError('remote raw identity conflict')
        present.append(True)
    if any(present) and not all(present):raise ValueError('partial remote TARGET effect')
    return bool(present) and all(present)

def audit_attempt(stage,attempt,before,quotes,remote_status,error=None):
    try:private_metadata(stage)
    except Exception:
        # Rejected links must not be followed by diagnostics or disk accounting.
        return {'attempt':attempt,'expected_quotes':quotes,'remote_status':remote_status,
                'error':error,'raw_evidence':attempt.get('raw_checkpoint',{}),
                'evidence_read':'NOT_VERIFIED: unsafe private paths were not dereferenced',
                'expected_commit':None,'observed_stage_head':None,'formal_prediction_issuance':False}
    data={}
    paths=[base.REG,base.SCORES,evidence.BINDINGS,'data/stage5/spot_plus/quote_watch_latest.json']
    paths+=sorted(p.relative_to(stage).as_posix() for p in (stage/evidence.BASE/'snapshots').glob('*/*.json')
                  if p.relative_to(stage).as_posix() not in before.get('snapshots',{}))
    for name in paths:
        p=stage/name
        try:
            if p.exists():
                kind={base.REG:('registry',frozen.FCOLS),base.SCORES:('scores',frozen.SCOLS),evidence.BINDINGS:('bindings',evidence.BCOLS)}.get(name)
                if kind:
                    previous=before.get(kind[0],{});current=frozen.read_rows(p,kind[1])
                    data[name]={'sha256':digest(p),'columns':kind[1],
                                'changed_rows':{key:{'before':previous.get(key),'after':current.get(key)} for key in previous.keys()|current.keys() if previous.get(key)!=current.get(key)}}
                else:data[name]={'sha256':digest(p),'utf8':p.read_text(encoding='utf-8')}
        except Exception as exc:data[name]={'evidence_read':'NOT_VERIFIED','error_type':type(exc).__name__}
    try:head=local_git(stage,'rev-parse','HEAD').decode().strip()
    except Exception:head=None
    return {'attempt':attempt,'expected_quotes':quotes,'remote_status':remote_status,
            'error':error,'raw_evidence':data,'formal_prediction_issuance':False,
            'expected_commit':attempt.get('expected_commits',[None])[-1] if attempt.get('expected_commits') else None,
            'observed_stage_head':head}

def strict_utc(value):
    if not isinstance(value,str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?(?:Z|\+00:00)',value):
        raise ValueError('canonical UTC evidence required')
    return frozen.utc(value)

def strict_24h(fid,row):
    match=re.fullmatch(re.escape(frozen.MODEL)+r'_([0-9]{13})_24h',fid)
    if (not match or row.get('forecast_id')!=fid or row.get('model_id')!=frozen.MODEL
            or row.get('horizon_hours')!='24'):
        raise ValueError('unauthorized 24H identity or period')
    issued=strict_utc(row['issued_at_utc']);target=strict_utc(row['target_utc'])
    source=strict_utc(row['source_hour_start_utc']);quoted=strict_utc(row['source_quote_utc'])
    bar=int(match[1])
    if (bar%3600000 or source!=datetime.fromtimestamp((bar+3600000)/1000,timezone.utc)
            or not 0<=(issued-source).total_seconds()<=frozen.MAX_ISSUE_DELAY_MIN*60
            or not 0<=(issued-quoted).total_seconds()<=frozen.MAX_QUOTE_AGE_SEC
            or target-issued!=timedelta(hours=24)):
        raise ValueError('24H issue, target or source identity mismatch')
    return target

def requested_ids(registry,expected_ids):
    if (not isinstance(expected_ids,list) or not 1<=len(expected_ids)<=100
            or any(not isinstance(fid,str) for fid in expected_ids)
            or len(set(expected_ids))!=len(expected_ids)):
        raise ValueError('explicit distinct 24H IDs required')
    for fid in expected_ids:
        if fid not in registry:raise ValueError('requested prediction not registered')
        strict_24h(fid,registry[fid])
    return set(expected_ids)

def eligible_ids(registry,scores,now,expected_ids=None):
    if now.tzinfo is None or now.utcoffset()!=timedelta(0):raise ValueError('UTC clock required')
    permitted=requested_ids(registry,expected_ids)
    return sorted(fid for fid,row in registry.items() if fid not in scores
                  and fid in permitted
                  and frozen.utc(row['target_utc'])<=now<=frozen.utc(row['target_utc'])+timedelta(minutes=20))

def capture_scope(registry,scores,expected_ids,quoted,observed):
    permitted=requested_ids(registry,expected_ids)
    if (observed.tzinfo is None or observed.utcoffset()!=timedelta(0)
            or quoted.tzinfo is None or quoted.utcoffset()!=timedelta(0)
            or not 0<=(observed-quoted).total_seconds()<=frozen.MAX_QUOTE_AGE_SEC):
        raise ValueError('invalid genuine quote clock')
    selected={fid:row for fid,row in registry.items() if fid not in scores
              and frozen.utc(row['target_utc'])<=quoted<=frozen.utc(row['target_utc'])+timedelta(minutes=25)}
    if not selected or not set(selected).issubset(permitted):
        raise ValueError('aggregate watch would escape requested 24H scope')
    for fid,row in selected.items():
        target=strict_24h(fid,row)
        if not target<=quoted<=observed<=target+timedelta(minutes=20):
            raise ValueError('capture consumed five-minute reserve')
    return selected

def collect_scoped(root,expected_ids,clock=None,audit_raw=None):
    """Validate the actual exchange quote and entire frozen watch batch before writes."""
    safe_stage(root)
    clock=clock or (lambda:datetime.now(timezone.utc))
    with evidence.urllib.request.urlopen(evidence.urllib.request.Request(evidence.URL,
            headers={'Accept':'application/json','User-Agent':'DOGE-evidence-v2'}),timeout=20) as response:
        raw=response.read().decode('utf-8')
    now=clock()
    payload={'schema_version':2,'source_url':evidence.URL,'observed_at_utc':now.isoformat(),
             'raw_response_utf8':raw,'transport':'HTTPS collector observation; not exchange-signed and not an executable fill'}
    price,quoted,_=evidence.parse(payload)
    registry=frozen.read_rows(root/base.REG,frozen.FCOLS);scores=frozen.read_rows(root/base.SCORES,frozen.SCOLS)
    bindings=frozen.read_rows(root/evidence.BINDINGS,evidence.BCOLS)
    selected=capture_scope(registry,scores,expected_ids,quoted,now)
    frozen.verify_append_only(root/base.REG,registry,frozen.FCOLS,root)
    frozen.verify_append_only(root/base.SCORES,scores,frozen.SCOLS,root)
    evidence.history_receipts(root,evidence.BINDINGS,evidence.BCOLS,bindings)
    planned,added=frozen.score(selected,{},price,quoted,now)
    if added!=len(selected) or any(s['status']!='scored' for s in planned.values()):
        raise ValueError('invalid planned target scoring')
    qid=hashlib.sha256((json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(',',':'))+'\n').encode('utf-8')).hexdigest()
    for fid,row in selected.items():
        if fid+'|TARGET' in bindings:raise ValueError('unscored target already has a residual binding')
        evidence.verify_bound(root,row,None,bindings)
        evidence.bind(bindings,fid,'TARGET',qid)
    if audit_raw:
        relative=evidence.snapshot_path(root,qid).relative_to(root).as_posix()
        audit_raw(root,{relative:{'sha256':qid,
            'utf8':json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(',',':'))+'\n'}})
    fresh=clock()
    if fresh<now or any(fresh>strict_24h(fid,row)+timedelta(minutes=20) for fid,row in selected.items()):
        raise ValueError('pre-write verification consumed five-minute reserve')
    # All deterministic scope/history/scoring/binding gates precede persistence.
    # I/O or publication errors still propagate; genuine evidence is never erased.
    if evidence.save_quote(root,raw,now)!=qid:raise ValueError('raw quote identity mismatch')
    out=evidence.watch.run(root,now,(price,quoted))
    if out['status']!='ok' or out['scored_forecast_ids']!=sorted(selected):
        raise ValueError('frozen watch result differs from prevalidated batch')
    frozen.write(root/evidence.BINDINGS,frozen.serial(bindings,evidence.BCOLS))
    return 0

def verify_formal_issues(root,registry,bindings,ids):
    """Reuse frozen remote-witness verification; never substitute a Git timestamp."""
    protocol=json.loads((root/v2.PROTOCOL).read_text(encoding='utf-8'))
    prior=frozen.read_rows(root/base.WITNESSES,base.WCOLS)
    base.history_receipts(root,base.WITNESSES,base.WCOLS,prior)
    if any(fid not in prior for fid in ids):raise ValueError('formal pre-target remote witness missing')
    subset={fid:registry[fid] for fid in ids}
    public=base.remote_witnesses(root,subset,{fid:prior[fid] for fid in ids},[],persist_cache=False)
    receipts=base.history_receipts(root,base.REG,frozen.FCOLS,registry)
    for fid,row in subset.items():
        issued=frozen.utc(row['issued_at_utc']);target=strict_24h(fid,row)
        stamp=frozen.utc(public[fid]['observed_public_utc'])
        if issued<frozen.utc(protocol['effective_from_utc']) or not issued-timedelta(seconds=1)<=stamp<=issued+timedelta(minutes=45) or stamp>=target:
            raise ValueError('prediction lacks eligible frozen formal publication')
        receipt=receipts.get(fid)
        committed=frozen.utc(receipt['committed_at_utc']) if receipt else None
        if committed is None or not issued-timedelta(seconds=1)<=committed<=issued+timedelta(minutes=45) or committed>=target:
            raise ValueError('prediction lacks eligible immutable commit receipt')
        evidence.verify_bound(root,row,None,bindings)
        evidence.verify_public_issue(root,row,bindings,public[fid]['head_sha'])

def ensure_clean_checkout(root):
    try:
        publisher.command(root,'diff','--exit-code','--quiet')
        publisher.command(root,'diff','--cached','--exit-code','--quiet')
    except subprocess.CalledProcessError as exc:raise ValueError('dirty tracked checkout or staged changes') from exc

def unsafe_aggregate_batch(registry,scores,now,expected_ids):
    # Frozen watch can use a quote up to 120 seconds old. Do not invoke it if
    # that quote could select an unscored row outside our five-minute reserve.
    allowed=set(eligible_ids(registry,scores,now,expected_ids))
    return any(fid not in scores and fid not in allowed
               and now-timedelta(minutes=27)<=frozen.utc(row['target_utc'])<=now
               for fid,row in registry.items())

def check_reserve(quotes,now):
    if any(now>frozen.utc(q['target_utc'])+timedelta(minutes=20) for q in quotes):
        raise ValueError('verification consumed five-minute reserve')

def guard_protocol(root):
    protocol=json.loads((root/v2.PROTOCOL).read_text(encoding='utf-8'))
    v2.check_protocol(protocol);v2.check_code(root,protocol)
    base.locked_file(root,v2.PROTOCOL);base.locked_file(root,base.PROTOCOL)
    if protocol['eligible_target_quote_window_minutes']!=25 or frozen.TARGET_GRACE_MIN!=25:
        raise ValueError('frozen target window differs from reviewed rescue contract')

def take_state(root):
    snapshots=root/evidence.BASE/'snapshots'
    return {'registry':frozen.read_rows(root/base.REG,frozen.FCOLS),
            'scores':frozen.read_rows(root/base.SCORES,frozen.SCOLS),
            'bindings':frozen.read_rows(root/evidence.BINDINGS,evidence.BCOLS),
            'snapshots':{p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in snapshots.glob('*/*.json')}}

def verify_capture(root,before,allowed,now):
    after=take_state(root)
    if after['registry']!=before['registry']:raise ValueError('rescue changed prediction registry')
    for name in ('scores','bindings','snapshots'):
        if any(after[name].get(k)!=value for k,value in before[name].items()):
            raise ValueError('existing '+name+' evidence changed')
    added=set(after['scores'])-set(before['scores'])
    if not added.issubset(allowed):raise ValueError('capture scored an expired or unauthorized target')
    if set(after['bindings'])-set(before['bindings'])!={fid+'|TARGET' for fid in added}:
        raise ValueError('unexpected new quote binding')
    quotes=[]
    for fid in sorted(added):
        row=after['registry'][fid];score=after['scores'][fid];target=frozen.utc(row['target_utc'])
        deadline=target+timedelta(minutes=frozen.TARGET_GRACE_MIN-RESERVED_MINUTES)
        quoted=frozen.utc(score['target_quote_utc']);observed=frozen.utc(score['observed_at_utc'])
        if score['status']!='scored' or not target<=quoted<=observed<=deadline or now>deadline:
            raise ValueError('capture or publication preparation consumed five-minute reserve')
        evidence.verify_bound(root,row,score,after['bindings'])
        quotes.append({'forecast_id':fid,'quote_id':after['bindings'][fid+'|TARGET']['quote_id'],
                       'target_utc':row['target_utc'],'target_quote_utc':score['target_quote_utc'],
                       'observed_at_utc':score['observed_at_utc']})
    return quotes

def _run_in_attempt(root,expected_ids=None,clock=None,capture_fn=None,publish_fn=None,audit_hook=None,metrics=None):
    root=Path(root);clock=clock or (lambda:datetime.now(timezone.utc))
    metrics={} if metrics is None else metrics;phase=time.monotonic()
    ensure_clean_checkout(root);guard_protocol(root)
    before=take_state(root);allowed=eligible_ids(before['registry'],before['scores'],clock(),expected_ids)
    if not allowed:return {'status':'skip_no_open_unscored_target','formal_prediction_issuance':False}
    publisher.validate_state(root)
    verify_formal_issues(root,before['registry'],before['bindings'],requested_ids(before['registry'],expected_ids))
    # Integrity validation and queueing use real time; stale input never permits backfill.
    capture_started=clock()
    if not eligible_ids(before['registry'],before['scores'],capture_started,expected_ids):
        return {'status':'skip_window_closed_during_validation','formal_prediction_issuance':False}
    if unsafe_aggregate_batch(before['registry'],before['scores'],capture_started,expected_ids):
        return {'status':'skip_unsafe_aggregate_watch_batch','formal_prediction_issuance':False}
    metrics['validation_before_capture_seconds']=time.monotonic()-phase;phase=time.monotonic()
    capture=(capture_fn(root) if capture_fn else retry.capture('watch',root=root,
        collect=lambda current_root,mode:collect_scoped(current_root,expected_ids,clock,
            (lambda raw_root,raw:audit_hook('raw',raw_root,raw)) if audit_hook else None)))
    metrics['capture_seconds']=time.monotonic()-phase;phase=time.monotonic()
    if capture.get('status')!='ok':raise ValueError('genuine quote capture failed: '+json.dumps(capture))
    guard_protocol(root)
    observed=clock()
    # Never expand an explicit request after collection.
    allowed=eligible_ids(before['registry'],before['scores'],observed,expected_ids)
    quotes=verify_capture(root,before,set(allowed),observed)
    captured_state=take_state(root)
    for quote in quotes:
        quote['score']=captured_state['scores'][quote['forecast_id']]
        quote['prediction']=captured_state['registry'][quote['forecast_id']]
    if audit_hook:audit_hook('captured',root,quotes)
    check_reserve(quotes,clock())
    if not quotes:return {'status':'skip_no_eligible_genuine_quote','capture':capture,'formal_prediction_issuance':False}
    def validate_before_push(current_root):
        if audit_hook:audit_hook('check',current_root,quotes)
        guard_protocol(current_root);publisher.validate_state(current_root)
        verified_quotes=verify_capture(current_root,before,set(allowed),clock())
        check_reserve(verified_quotes,clock())
    validate_before_push(root)
    metrics['postcapture_validation_seconds']=time.monotonic()-phase
    publisher.command(root,'config','user.name','github-actions[bot]')
    publisher.command(root,'config','user.email','41898282+github-actions[bot]@users.noreply.github.com')
    if audit_hook:audit_hook('publishing',root,quotes)
    phase=time.monotonic()
    try:result=(publish_fn or publisher.publish)(root,validate=validate_before_push)
    finally:metrics['publication_including_retry_readback_seconds']=time.monotonic()-phase
    if (result.get('status')!='verified_remote_readback' or result.get('new_predictions') is not False
            or not result.get('remote_head') or not result.get('verified_at_utc')):
        raise ValueError('independent remote readback proof missing or unexpected issuance')
    verified=frozen.utc(result['verified_at_utc'])
    in_window=all(verified<=frozen.utc(q['target_utc'])+timedelta(minutes=25) for q in quotes)
    return {'status':'verified_window_quote_rescue' if in_window else 'verified_remote_readback_after_window',
            'scored_ids':[q['forecast_id'] for q in quotes],'quotes':quotes,'capture':capture,
            'publication':result,'remote_readback_within_window':in_window,
            'formal_prediction_issuance':False,'real_orders_authorized':False,'historical_backfill':False}

def run(root,expected_ids=None,clock=None,capture_fn=None,publish_fn=None):
    """The canonical checkout is never the collector or publisher's worktree."""
    root=Path(root).absolute()
    if root.is_symlink() or root.resolve()!=root:raise ValueError('redirected source root')
    clock=clock or (lambda:datetime.now(timezone.utc))
    check_git_environment()
    probe_env=dict(os.environ,GIT_CONFIG_NOSYSTEM='1',GIT_CONFIG_GLOBAL=os.devnull,GIT_OPTIONAL_LOCKS='0')
    common=Path(subprocess.run(['git','--no-optional-locks','rev-parse','--path-format=absolute','--git-common-dir'],
        cwd=root,check=True,capture_output=True,timeout=30,env=probe_env).stdout.decode().strip()).resolve()
    git_dir=Path(subprocess.run(['git','--no-optional-locks','rev-parse','--absolute-git-dir'],
        cwd=root,check=True,capture_output=True,timeout=30,env=probe_env).stdout.decode().strip()).resolve()
    parent=Path(tempfile.gettempdir()).resolve()
    if any(parent==p or p in parent.parents for p in (root,git_dir,common)):
        raise ValueError('temporary parent lies inside canonical business or Git state')
    home=Path(tempfile.mkdtemp(prefix='doge24-',dir=parent)).resolve()
    if home==root or root in home.parents:raise ValueError('attempt directory inside canonical root')
    started=time.monotonic();attempt={'path':str(home),'timings':{}};stage=None;quotes=[];publishing=False
    with private_git_environment(home):
        source_state=take_state(root);source_business=business_identity(root);source_meta=metadata(root)
        identity=code_identity()
        ensure_clean_checkout(root)
        eligible=eligible_ids(source_state['registry'],source_state['scores'],clock(),expected_ids)
        if not eligible:return {'status':'skip_no_open_unscored_target','formal_prediction_issuance':False}
        safe_audit(root,{'status':'blocked','error':'Attempt incomplete; not a successful rescue',
                         'attempt':attempt,'expected_ids':expected_ids,'remote_status':'NOT_ATTEMPTED'})
        try:
            stage,attempt=create_attempt(root,home,identity)
            attempt['timings']={'prepare_seconds':time.monotonic()-started}
            before=take_state(stage)
            def hook(event,current,observed_quotes):
                nonlocal quotes,publishing
                private_metadata(current)
                if event=='raw':
                    attempt['raw_checkpoint']=observed_quotes
                    safe_audit(root,{'status':'blocked','error':'Attempt incomplete; not a successful rescue',
                                    'attempt':attempt,'raw_evidence':observed_quotes,'remote_status':'NOT_ATTEMPTED',
                                    'expected_ids':expected_ids})
                    return
                quotes=observed_quotes
                if event=='publishing':publishing=True
                if code_identity()!=identity:raise ValueError('executing candidate code changed during attempt')
                if any(digest(current/name)!=value for name,value in attempt['disk_code'].items()):
                    raise ValueError('production code changed during pull/rebase')
                # origin/main reflects pull's actual remote base. Only this
                # evidence commit may be ahead; no local candidate ancestors.
                permitted={base.SCORES,evidence.BINDINGS}|{
                    evidence.snapshot_path(current,q['quote_id']).relative_to(current).as_posix() for q in quotes}
                changed=set(local_git(current,'diff','--name-only','origin/main').decode().splitlines())
                if not changed.issubset(permitted):raise ValueError('publication includes unauthorized paths')
                ahead=local_git(current,'rev-list','origin/main..HEAD').decode().splitlines()
                if len(ahead)>1:raise ValueError('publication includes unrelated commits')
                attempt.setdefault('expected_commits',[])
                for head in ahead:
                    if head not in attempt['expected_commits']:attempt['expected_commits'].append(head)
                if event=='check' and publishing and remote_effect(current,'FETCH_HEAD',quotes):
                    raise ValueError('remote already contains this TARGET effect; stop internal retry')
                safe_audit(root,dict(status='blocked',**audit_attempt(current,attempt,before,quotes,
                    'NOT_VERIFIED' if publishing else 'NOT_ATTEMPTED')))
            result=_run_in_attempt(stage,expected_ids,clock,capture_fn,publish_fn,hook,attempt['timings'])
            attempt['timings']['total_seconds']=time.monotonic()-started
            attempt['disk_bytes']=attempt_bytes(home)
            if business_identity(root)!=source_business or metadata(root)!=source_meta:
                raise ValueError('canonical business or Git state changed; never restore over concurrent work')
            result['attempt']=attempt
            safe_audit(root,result)
            return result
        except Exception as exc:
            remote_status='NOT_ATTEMPTED'
            if publishing:
                remote_status='NOT_VERIFIED'
                # A single read-only reconciliation, never a new capture/push.
                # Later normal HEADs are checked by business content identity.
                try:
                    private_metadata(stage)
                    local_git(stage,'fetch','origin','main')
                    remote_status='CONFIRMED_PRESENT' if remote_effect(stage,'FETCH_HEAD',quotes) else 'CONFIRMED_ABSENT'
                    attempt['reconciled_remote_head']=local_git(stage,'rev-parse','FETCH_HEAD').decode().strip()
                    ancestry={}
                    for head in attempt.get('expected_commits',[]):
                        try:local_git(stage,'merge-base','--is-ancestor',head,'FETCH_HEAD');ancestry[head]=True
                        except subprocess.CalledProcessError:ancestry[head]=False
                    attempt['reconciled_commit_ancestry']=ancestry
                    attempt['remote_commit_status']='CONFIRMED_ACCEPTED' if any(ancestry.values()) else 'NOT_VERIFIED'
                except Exception:pass
            attempt['timings']['total_seconds']=time.monotonic()-started
            attempt['disk_bytes']=attempt_bytes(home)
            message=type(exc).__name__+': '+str(exc)[:500]
            # Never serialize subprocess argv (may contain private config).
            if isinstance(exc,subprocess.SubprocessError):message=type(exc).__name__+': Git operation failed; private state retained'
            record=(audit_attempt(stage,attempt,before,quotes,remote_status,message) if stage and 'before' in locals()
                    else {'attempt':attempt,'remote_status':remote_status,'error':message})
            record['status']='blocked';record['expected_ids']=expected_ids
            try:record['canonical_unchanged']=business_identity(root)==source_business and metadata(root)==source_meta
            except Exception:record['canonical_unchanged']='NOT_VERIFIED'
            try:safe_audit(root,record)
            except Exception as audit_error:record['audit_error']=type(audit_error).__name__
            raise RescueFailure(message,record) from exc

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--expected-ids-json',required=True);args=parser.parse_args()
    try:
        expected=json.loads(args.expected_ids_json)
        if (not isinstance(expected,list) or not 1<=len(expected)<=100 or len(expected)!=len(set(expected))
                or any(not isinstance(x,str) or not x or len(x)>200 for x in expected)):
            raise ValueError('expected IDs must be a nonempty bounded list of distinct strings')
        out=run(Path(__file__).resolve().parent,expected)
    except RescueFailure as exc:out=exc.evidence
    except Exception as exc:out={'status':'blocked','error':type(exc).__name__+': '+str(exc)[:700],'real_orders_authorized':False}
    try:safe_audit(Path(__file__).resolve().parent,out)
    except Exception as exc:
        print(json.dumps({'status':'blocked','error':'Audit output failed','error_type':type(exc).__name__}))
        return 1
    print(json.dumps({key:value for key,value in out.items() if key not in ('raw_evidence','attempt')},ensure_ascii=False))
    return 0 if out['status'].startswith('skip_') or out['status']=='verified_window_quote_rescue' else 1

if __name__=='__main__':raise SystemExit(main())
