"""Explicit R2 engineering versions; original science, clocks and history preserved."""
import base64,hashlib,json,re,subprocess
from datetime import datetime,timezone
from urllib.error import HTTPError
RUNTIME='phase2_forward_runtime_v2.py'
HELPER='phase2_runtime_engineering_v1.py'
MANIFEST='config/phase2_forward_engineering_v1.json'
REGISTRATION='config/phase2_forward_registration_v2.json'
SCHEMA='DOGE_R2_ENGINEERING_COMPATIBILITY_V1'
APPROVED='APPROVED_ENGINEERING_COMPATIBILITY'
CANDIDATE='OFFLINE_CANDIDATE'
ORIGINAL_REGISTRATION_SHA256='d0aa618d6336dea8222f6187e4b14ca8f20a60dd98714f80c48b27009f3e6103'
ORIGINAL_RUNTIME_SHA256='518d1589f4a506f27d6e6e7735c9a52fbc53ff13035fc0f11e3486d6fc9d59c5'
ORIGINAL_MAP_SHA256='0b1736fb72c86d7fdeda492936a593a7996fd1597b70d73a61289ce0f2b0be73'
LEGACY_ANCHOR='2c5068902abb320f9a45ecc0de1d49e66eaa0be9'
LEGACY_MAP_SHA256='ffc115cb8f040f29e6568642b56acfb48c9ffe75b8f5ba2cdb7d9dc0dab33197'
KEYS={'schema','authorization','approved_at_utc','activation_utc','protocol_id','model_identity',
'original_registration_sha256','original_sha256_lf','original_runtime_base64','runtime_sha256_lf',
'helper_sha256_lf','legacy_anchor_commit','legacy_event_git_blobs','model_changed',
'ids_or_denominators_reset','deadlines_changed'}
def sha(raw):return hashlib.sha256(raw).hexdigest()
def map_sha(value):return sha(json.dumps(value,sort_keys=True,separators=(',',':')).encode())
def blob(raw):return hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
def _utc(raw):
    if not isinstance(raw,str):raise ValueError('engineering UTC absent')
    x=datetime.fromisoformat(raw.replace('Z','+00:00'))
    if x.tzinfo is None or x.utcoffset().total_seconds()!=0:raise ValueError('engineering UTC invalid')
    return x.astimezone(timezone.utc)
def _parse(raw):
    m=json.loads(raw)
    if not isinstance(m,dict) or set(m)!=KEYS or m['schema']!=SCHEMA:raise ValueError('engineering manifest schema changed')
    if m['original_registration_sha256']!=ORIGINAL_REGISTRATION_SHA256 or map_sha(m['original_sha256_lf'])!=ORIGINAL_MAP_SHA256 or m['original_sha256_lf'].get(RUNTIME)!=ORIGINAL_RUNTIME_SHA256:raise ValueError('pinned original registration changed')
    if m['legacy_anchor_commit']!=LEGACY_ANCHOR or map_sha(m['legacy_event_git_blobs'])!=LEGACY_MAP_SHA256:raise ValueError('pinned legacy identity set changed')
    if m['authorization'] not in (CANDIDATE,APPROVED):raise ValueError('engineering authority absent')
    if any(m[k] is not False for k in ('model_changed','ids_or_denominators_reset','deadlines_changed')):raise ValueError('engineering scope exceeded')
    if not re.fullmatch('[a-f0-9]{40}',m['legacy_anchor_commit']):raise ValueError('engineering history anchor invalid')
    if not isinstance(m['legacy_event_git_blobs'],dict) or any(re.fullmatch(r'[0-9]+_(formal|canary)',k) is None or not isinstance(v,str) or re.fullmatch('[a-f0-9]{40}',v) is None for k,v in m['legacy_event_git_blobs'].items()):raise ValueError('engineering legacy identities invalid')
    if m['authorization']==APPROVED:
        if _utc(m['approved_at_utc'])>_utc(m['activation_utc']):raise ValueError('activation predates approval')
    elif m['approved_at_utc'] is not None or m['activation_utc'] is not None:raise ValueError('candidate cannot claim approval')
    return m
def _context(files):
    if MANIFEST not in files:return None
    m=_parse(files[MANIFEST])
    if not {HELPER,RUNTIME,REGISTRATION}<=files.keys():raise ValueError('engineering proof incomplete')
    if sha(files[RUNTIME])!=m['runtime_sha256_lf'] or sha(files[HELPER])!=m['helper_sha256_lf']:raise ValueError('engineering executable changed')
    if sha(files[REGISTRATION])!=m['original_registration_sha256']:raise ValueError('old registration changed')
    old=base64.b64decode(m['original_runtime_base64'],validate=True)
    if sha(old)!=m['original_sha256_lf'][RUNTIME]:raise ValueError('old runtime payload changed')
    return m,old
def verify_files(root,registration,actual,read_file):
    hashes=registration.get('sha256_lf',{})
    if set(actual)!=set(hashes):raise ValueError('exact registered file set required')
    changed={p for p in actual if sha(actual[p])!=hashes[p]}
    if not changed:return actual
    if changed!={RUNTIME}:raise ValueError('registered source changed outside engineering runtime')
    raw=read_file(root,MANIFEST);m=_parse(raw)
    if m['original_sha256_lf']!=hashes:raise ValueError('old 27-file identity changed')
    if m['protocol_id']!=registration['protocol_id'] or m['model_identity']!=registration['model_identity']:raise ValueError('model/protocol changed')
    current={**actual,HELPER:read_file(root,HELPER),MANIFEST:raw,REGISTRATION:read_file(root,REGISTRATION)}
    _context(current)
    del current[REGISTRATION]
    return current
def original_files(files):
    c=_context(files)
    if c is None:return files
    answer={p:b for p,b in files.items() if p not in (HELPER,MANIFEST)}
    answer[RUNTIME]=c[1]
    return answer
def is_legacy(e,files,encode):
    c=_context(files)
    if c is None:return True
    expected=c[0]['legacy_event_git_blobs'].get(e['event_id'])
    if expected is None:return False
    if blob(encode(e))!=expected:raise ValueError('legacy event bytes changed')
    return True
def event_files(e,files,utc,encode):
    if is_legacy(e,files,encode):return original_files(files)
    m,_=_context(files)
    if m['authorization']!=APPROVED or utc(e['issued_at_utc'])<_utc(m['activation_utc']):raise ValueError('unapproved/pre-activation new event')
    return files
def source_files(e,files,raw,source,utc,encode):
    proof=event_files(e,files,utc,encode)
    return {source:raw} if is_legacy(e,files,encode) else {**proof,source:raw}
def require_execution_mode(files,mode):
    c=_context(files)
    if c is not None and c[0]['authorization']!=APPROVED and mode not in ('local','audit','audit_all'):
        raise ValueError('offline engineering candidate cannot write')
def require_dispatch(root,read_file):
    files={p:read_file(root,p) for p in (RUNTIME,HELPER,MANIFEST,REGISTRATION)}
    c=_context(files)
    if c is None or c[0]['authorization']!=APPROVED:raise ValueError('offline engineering candidate cannot dispatch')
def source_after(e,files,encode):
    c=_context(files)
    return None if c is None or is_legacy(e,files,encode) else _utc(c[0]['approved_at_utc'])
def issuance_source_after(files):
    c=_context(files)
    return None if c is None else _utc(c[0]['approved_at_utc'])
def can_issue(files,at,utc):
    c=_context(files)
    if c is None:return True
    m=c[0]
    return m['authorization']==APPROVED and utc(at)>=_utc(m['activation_utc'])
def issuance_source_files(files,raw,source,at,utc):
    if not can_issue(files,at,utc):raise ValueError('engineering issuance not active')
    return {source:raw} if _context(files) is None else {**files,source:raw}
def immutable_object_absent(root,head,path):
    if not isinstance(head,str) or re.fullmatch('[a-f0-9]{40}',head) is None:return False
    if not isinstance(path,str) or re.fullmatch(r'data/phase2_forward_v2/(events|scores)/[0-9]+_(formal|canary)\.json',path) is None:return False
    try:
        commit=subprocess.run(['git','--no-replace-objects','-c','safe.directory=*','cat-file','-e',head+'^{commit}'],cwd=root,capture_output=True,timeout=5)
        if commit.returncode:return False
        tree=subprocess.run(['git','--no-replace-objects','-c','safe.directory=*','ls-tree','--full-tree','-z',head,'--',path],cwd=root,capture_output=True,timeout=5)
        return tree.returncode==0 and tree.stdout==b''
    except (OSError,subprocess.SubprocessError):return False
class WitnessObjectAbsent(ValueError):pass
def read_at(reader,root,head,path):
    try:return reader(head,path)
    except HTTPError as exc:
        if exc.code==404 and immutable_object_absent(root,head,path):
            raise WitnessObjectAbsent('immutable witness event/score object absent') from exc
        raise
