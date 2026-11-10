"""Bind post-upload publication to the exact preparer captured before rebase."""
import argparse,hashlib,json,os,re,sys,zipfile
from pathlib import Path
import runtime_process

BASE='data/pit_retention_preparation_v1'
SOURCE='pit_retention_preparation.py'
HEX=re.compile('[a-f0-9]{40}')
SHA=re.compile('[a-f0-9]{64}')
CODE_CAP=256*1024
REPO='lzwislam-collab/doge-data-collector'

def digest(raw):return hashlib.sha256(raw).hexdigest()
def blob(raw):return hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
def unique(items):
 out={}
 for k,v in items:
  if k in out:raise ValueError('duplicate preparation binding key')
  out[k]=v
 return out
def parse(raw):return json.loads(raw,object_pairs_hook=unique)
def read_json(path,cap=2*1024*1024):
 path=plain(path)
 if not path.is_file() or path.stat().st_size>cap:raise ValueError('bounded regular preparation metadata required')
 return path.read_bytes()
def plain(path):
 path=Path(path).absolute()
 for p in [path,*path.parents]:
  if p.is_symlink() or getattr(p,'is_junction',lambda:False)():raise ValueError('prepared implementation symlink/junction rejected')
 return path.resolve()
def context():
 run,attempt=os.environ.get('GITHUB_RUN_ID',''),os.environ.get('GITHUB_RUN_ATTEMPT','')
 if (os.environ.get('GITHUB_REF')!='refs/heads/main' or os.environ.get('GITHUB_REPOSITORY')!=REPO
     or not re.fullmatch('[1-9][0-9]*',run) or not re.fullmatch('[1-9][0-9]*',attempt)):
  raise ValueError('trusted same-run main preparation context required')
 return run,attempt
def control():
 run,attempt=context();temp=os.environ.get('RUNNER_TEMP','')
 if not temp or not Path(temp).is_absolute():raise ValueError('absolute runner temporary directory required')
 return plain(Path(temp)/('doge-pit-prepared-'+run+'-'+attempt))
def git(root,*args):
 env=dict(os.environ,GIT_NO_LAZY_FETCH='1')
 return runtime_process.command(('git','--no-replace-objects','-c','protocol.allow=never',*args),cwd=root,env=env,timeout=5).strip()
def source_entry(root,head):
 if not HEX.fullmatch(head):raise ValueError('invalid captured preparation commit')
 row=git(root,'ls-tree',head,'--',SOURCE);parts=row.split('\t')
 if len(parts)!=2 or parts[1]!=SOURCE:raise ValueError('captured preparation source missing')
 mode,kind,sha=parts[0].split(' ')
 if mode not in ('100644','100755') or kind!='blob' or not HEX.fullmatch(sha):raise ValueError('invalid captured preparation tree identity')
 return sha
def read_code(path):
 path=plain(path)
 if not path.is_file() or path.stat().st_size>CODE_CAP:raise ValueError('bounded regular preparation implementation required')
 return path.read_bytes()
def exclusive(path,value):
 with plain(path).open('xb') as f:f.write(value)
def json_bytes(value):return (json.dumps(value,sort_keys=True,indent=2)+'\n').encode()

def capture(root):
 root=plain(root);run,attempt=context();c=control();c.mkdir(mode=0o700)
 raw=read_code(root/SOURCE);head=git(root,'rev-parse','--verify','HEAD^{commit}');sha=source_entry(root,head)
 if blob(raw)!=sha:raise ValueError('preparer working bytes differ from real captured HEAD')
 meta={'schema_version':1,'root':str(root),'run_id':run,'run_attempt':attempt,'source_head':head,
       'source_git_blob':sha,'source_raw_sha256':digest(raw),'implementation_sha256':digest(raw.replace(b'\r\n',b'\n'))}
 exclusive(c/'implementation.py',raw);exclusive(c/'capture.json',json_bytes(meta))
 return {'status':'PREPARATION_IMPLEMENTATION_CAPTURED','implementation_sha256':meta['implementation_sha256']}

def binding(root):
 root=plain(root);run,attempt=context();c=control();meta_raw=read_json(c/'capture.json');m=parse(meta_raw);raw=read_code(c/'implementation.py')
 if (m.get('schema_version')!=1 or m.get('root')!=str(root) or m.get('run_id')!=run or m.get('run_attempt')!=attempt
     or m.get('source_git_blob')!=blob(raw) or m.get('source_raw_sha256')!=digest(raw)
     or m.get('implementation_sha256')!=digest(raw.replace(b'\r\n',b'\n'))
     or source_entry(root,m.get('source_head',''))!=blob(raw)):
  raise ValueError('captured preparation source binding changed')
 return c,m,meta_raw,raw
def prior(root,m):
 raw=read_json(root/BASE/'latest.json');p=parse(raw)
 if (p.get('status')!='NOT_CONFIGURED' or p.get('generated_new_bundle') is not True
     or p.get('run_id')!=m['run_id'] or p.get('run_attempt')!=m['run_attempt']
     or p.get('implementation_sha256')!=m['implementation_sha256']):raise ValueError('same-run prepared implementation receipt required')
 return raw,p
def bundle_path(root,name):
 if (not isinstance(name,str) or not re.fullmatch(re.escape(BASE)+r'/bundles/[a-f0-9]{64}(?:\.zip|\.index\.json)',name)):
  raise ValueError('unsafe preparation bundle path')
 return plain(root/name)
def file_digest(path):
 h=hashlib.sha256()
 with path.open('rb') as f:
  for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
 return h.hexdigest()
def bundle_source(root,p,raw):
 path=bundle_path(root,p['bundle_path'])
 if not SHA.fullmatch(str(p.get('bundle_sha256',''))) or file_digest(path)!=p['bundle_sha256'] or path.stat().st_size!=p['byte_count']:
  raise ValueError('prepared bundle descriptor changed')
 if p.get('bundle_kind')=='SHARDED_INVENTORY_INDEX':
  if path.stat().st_size>32*1024*1024:raise ValueError('oversized inventory descriptor')
  index=parse(path.read_bytes())
  if (index.get('bundle_kind')!='SHARDED_INVENTORY_INDEX' or index.get('objects',{}).get(SOURCE)!=digest(raw)
      or not isinstance(index.get('parts'),list) or not 2<=len(index['parts'])<=64):raise ValueError('prepared source absent from inventory index')
  parts=index['parts']
 else:parts=[p]
 found=0
 for part in parts:
  zpath=bundle_path(root,part['bundle_path'])
  with zipfile.ZipFile(zpath) as z:
   names=z.namelist()
   if SOURCE not in names:continue
   if names.count(SOURCE)!=1 or z.getinfo(SOURCE).file_size>CODE_CAP:raise ValueError('ambiguous prepared source payload')
   if z.getinfo('bundle_manifest.json').file_size>16*1024*1024:raise ValueError('oversized part descriptor')
   detail=parse(z.read('bundle_manifest.json'))
   if (file_digest(zpath)!=part['bundle_sha256'] or zpath.stat().st_size!=part['byte_count']
       or detail.get('objects',{}).get(SOURCE)!=digest(raw) or z.read(SOURCE)!=raw):raise ValueError('prepared source payload differs from captured implementation')
   found+=1
 if found!=1:raise ValueError('exactly one real prepared implementation payload required')

def seal(root):
 root=plain(root);c,m,meta_raw,code=binding(root)
 run=parse(read_json(root/BASE/'run_latest.json'))
 if run.get('generated_new_bundle') is False:
  if run.get('status')!='SKIP_ALREADY_PREPARED_TODAY':raise ValueError('unqualified preparation cannot be bound')
  return {'status':'SKIP_NO_NEW_PREPARATION'}
 raw,p=prior(root,m)
 if read_code(root/SOURCE)!=code or plain(root/BASE/'run_latest.json').read_bytes()!=raw:
  raise ValueError('actual prepare source or run receipt drifted before binding')
 bundle_source(root,p,code)
 out={'schema_version':1,'run_id':m['run_id'],'run_attempt':m['run_attempt'],'capture_sha256':digest(meta_raw),
      'implementation_sha256':m['implementation_sha256'],'preparation_sha256':digest(raw),'bundle_sha256':p['bundle_sha256']}
 exclusive(c/'sealed.json',json_bytes(out));return {'status':'EXACT_PREPARED_IMPLEMENTATION_BOUND'}

def record_command(root,artifact_id,artifact_digest):
 root=plain(root);c,m,meta_raw,code=binding(root);sealed=parse(read_json(c/'sealed.json'));raw,p=prior(root,m)
 if (sealed.get('schema_version')!=1 or sealed.get('run_id')!=m['run_id'] or sealed.get('run_attempt')!=m['run_attempt']
     or sealed.get('capture_sha256')!=digest(meta_raw) or sealed.get('implementation_sha256')!=m['implementation_sha256']
     or sealed.get('preparation_sha256')!=digest(raw) or sealed.get('bundle_sha256')!=p['bundle_sha256']):raise ValueError('sealed prepared publication binding changed')
 snap=plain(root/'archive-snapshot');uploaded=read_json(snap/BASE/'latest.json')
 freeze=parse(read_json(snap/'manifest.json',32*1024*1024))
 if uploaded!=raw or freeze.get('file_sha256',{}).get(BASE+'/latest.json')!=digest(raw):raise ValueError('actual uploaded snapshot preparation differs')
 aid=str(artifact_id);ad=str(artifact_digest).removeprefix('sha256:')
 if not re.fullmatch('[1-9][0-9]*',aid) or not SHA.fullmatch(ad):raise ValueError('valid successful upload outputs required')
 bundle_source(root,p,code)
 return [sys.executable,str(c/'implementation.py'),'--root',str(root),'record-artifact','--artifact-id',aid,'--artifact-digest',str(artifact_digest),'--run-id',m['run_id']]

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,default=Path(__file__).resolve().parent)
 sub=p.add_subparsers(dest='mode',required=True);sub.add_parser('capture');sub.add_parser('bind');r=sub.add_parser('record-artifact');r.add_argument('--artifact-id',required=True);r.add_argument('--artifact-digest',required=True);r.add_argument('--run-id',required=True);args=p.parse_args()
 with runtime_process.cancellation_signals():
  if args.mode=='record-artifact':
   if args.run_id!=context()[0]:raise ValueError('upload run differs from captured run')
   command=record_command(args.root,args.artifact_id,args.artifact_digest);os.execv(command[0],command)
  else:print(json.dumps(capture(args.root) if args.mode=='capture' else seal(args.root)))
if __name__=='__main__':main()
