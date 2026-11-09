"""Trusted-main fast-forward with a 45s budget plus owned bounded pipe cleanup."""
import json
import os
from pathlib import Path
import re
import time
import runtime_process

ROOT=Path(__file__).resolve().parent
REPO='lzwislam-collab/doge-data-collector'
TIMEOUT_SECONDS=45

def git(root,deadline,*args):
 remaining=deadline-time.monotonic()
 if remaining<=0:raise TimeoutError('custody refresh budget exhausted')
 return runtime_process.command(['git','-c','core.hooksPath='+os.devnull,*args],
  cwd=root,timeout=remaining).strip()

def clean(root,deadline):
 if git(root,deadline,'status','--porcelain=v1','--untracked-files=all','--ignored=matching'):
  raise ValueError('custody refresh requires a clean checkout')

def fetch_main(root,deadline):
 # Update only the fetch locator. No force, reset, rebase or retry loop.
 git(root,deadline,'fetch','--no-tags','origin','refs/heads/main')
 return git(root,deadline,'rev-parse','--verify','FETCH_HEAD^{commit}')

def refresh(root=ROOT,timeout_seconds=TIMEOUT_SECONDS,expected_origin=None):
 if not 0<timeout_seconds<=TIMEOUT_SECONDS:raise ValueError('invalid custody refresh budget')
 root=Path(root).resolve();deadline=time.monotonic()+timeout_seconds
 if git(root,deadline,'symbolic-ref','--short','HEAD')!='main':raise ValueError('custody refresh requires main branch')
 if expected_origin is not None and git(root,deadline,'remote','get-url','origin') not in expected_origin:
  raise ValueError('custody refresh origin identity changed')
 clean(root,deadline);before=git(root,deadline,'rev-parse','HEAD');after=fetch_main(root,deadline)
 if not all(re.fullmatch('[a-f0-9]{40}',v) for v in (before,after)):raise ValueError('invalid custody commit identity')
 clean(root,deadline)
 if git(root,deadline,'rev-parse','HEAD')!=before:raise ValueError('custody HEAD changed during fetch')
 # This fails closed if main was rewritten or the local branch diverged.
 git(root,deadline,'merge-base','--is-ancestor',before,after)
 git(root,deadline,'merge','--ff-only','--no-edit','--no-stat',after)
 clean(root,deadline)
 if git(root,deadline,'rev-parse','HEAD')!=after:raise ValueError('custody fast-forward readback mismatch')
 return {'status':'TRUSTED_MAIN_FAST_FORWARD_VERIFIED','before_commit':before,'source_commit':after}

def main():
 out=ROOT/'forecast-worm-output'
 try:
  if os.environ.get('GITHUB_REF')!='refs/heads/main' or os.environ.get('GITHUB_REPOSITORY')!=REPO:
   raise ValueError('custody refresh restricted to trusted main job')
  with runtime_process.cancellation_signals():
   result=refresh(expected_origin={'https://github.com/'+REPO,'https://github.com/'+REPO+'.git'})
 except (Exception,KeyboardInterrupt,runtime_process.RuntimeCancelled) as exc:
  result={'status':'BLOCKED_CUSTODY_MAIN_REFRESH','error_type':type(exc).__name__}
  if getattr(exc,'process_cleanup',None):result['process_cleanup']=exc.process_cleanup
  out.mkdir(exist_ok=True);(out/'main_refresh.json').write_text(json.dumps(result,sort_keys=True)+'\n',encoding='utf-8')
  print(json.dumps(result,sort_keys=True));return 1
 out.mkdir(exist_ok=True);(out/'main_refresh.json').write_text(json.dumps(result,sort_keys=True)+'\n',encoding='utf-8')
 # A single SHA lets the existing shell pin the exact verified refresh result.
 print(result['source_commit']);return 0

if __name__=='__main__':raise SystemExit(main())
