"""Targeted offline receipt repair; never alters tested source or old evidence."""
import ast,contextlib,hashlib,json,os,socket,sys,unittest
from datetime import datetime,timezone
from pathlib import Path
from unittest.mock import patch

out=Path(__file__).resolve().parent
repo=out.parent/'repository'
manifest=json.loads((repo/'offline_acceptance/pr75_linux/manifest.json').read_bytes())

def check():
    observed={}
    for group in ('source_sha256_lf','frozen_files'):
        for name,expected in manifest[group].items():
            raw=(repo/name).read_bytes()
            observed[group+':'+name]=hashlib.sha256(raw).hexdigest()
            normalized=raw if name in manifest['frozen_binary_exact'] else raw.replace(b'\r\n',b'\n')
            if hashlib.sha256(normalized).hexdigest()!=expected:
                raise RuntimeError('input mismatch: '+name)
    return observed

tree=ast.parse((repo/'test_lean_version_gate.py').read_text(encoding='utf-8-sig'))
cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='VersionBoundary')
expected=['test_lean_version_gate.VersionBoundary.'+name for name in sorted(n.name for n in cls.body if isinstance(n,ast.FunctionDef) and n.name.startswith('test_'))]
report={'schema':'PR75_LEAN_OFFLINE_RECHECK_V1','status':'NOT_VERIFIED','tested_commit':'ac75d638daf85dc4c7540892786b54b5d5f3fcdf','reviewed_pr_commit':'24b424c05d5f8066556e01d9c05b438a1fc42f37','started_at_utc':datetime.now(timezone.utc).isoformat(),'platform':sys.platform,'python_version':sys.version,'python_optimization':sys.flags.optimize,'expected_ids':expected,'executed_ids':[],'passed_ids':[],'network_blocked':True,'reason':'Historical lean-module optimized pass raw log not recovered; this is a new targeted offline receipt, not reconstructed historical execution.'}

class Result(unittest.TextTestResult):
    def startTest(self,test):
        report['executed_ids'].append(test.id())
        super().startTest(test)
    def addSuccess(self,test):
        report['passed_ids'].append(test.id())
        super().addSuccess(test)

before=check()
sys.path.insert(0,str(repo))
with contextlib.ExitStack() as stack:
    for target,names in ((socket.socket,('connect','connect_ex','sendto','sendmsg')),(socket,('getaddrinfo','create_connection','gethostbyname','gethostbyname_ex'))):
        for name in names:
            if hasattr(target,name):stack.enter_context(patch.object(target,name,side_effect=AssertionError('offline network forbidden')))
    stack.enter_context(patch.dict(os.environ,{'GIT_NO_LAZY_FETCH':'1','GIT_TERMINAL_PROMPT':'0'}))
    stack.enter_context(contextlib.chdir(repo))
    with (out/'lean-offline-recheck.log').open('x',encoding='utf8') as log:
        with contextlib.redirect_stdout(log),contextlib.redirect_stderr(log):
            result=unittest.TextTestRunner(stream=log,verbosity=2,resultclass=Result).run(unittest.defaultTestLoader.loadTestsFromNames(expected))
after=check()
report.update(tests_run=result.testsRun,failures=[[str(t),trace] for t,trace in result.failures],errors=[[str(t),trace] for t,trace in result.errors],skipped=[[str(t),reason] for t,reason in result.skipped],inputs_unchanged=before==after,input_hash_entries=len(before),input_hashes_before=before,input_hashes_after=after,log_sha256=hashlib.sha256((out/'lean-offline-recheck.log').read_bytes()).hexdigest(),finished_at_utc=datetime.now(timezone.utc).isoformat())
passed=(result.wasSuccessful() and not result.skipped and result.testsRun==12 and report['executed_ids']==expected and report['passed_ids']==expected and before==after and sys.flags.optimize==1 and sys.dont_write_bytecode)
if passed:report['status']='PASS'
with (out/'lean-offline-recheck.json').open('x',encoding='utf8') as stream:json.dump(report,stream,indent=2);stream.write('\n')
print(json.dumps({k:report[k] for k in ('status','tests_run','inputs_unchanged','python_optimization','log_sha256','finished_at_utc')}))
raise SystemExit(0 if passed else 1)
