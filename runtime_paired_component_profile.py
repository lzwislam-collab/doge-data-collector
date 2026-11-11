"""One fixed-source read-only diagnostic; never satisfies the original 140 gate."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import pstats
import subprocess
import sys
import time

HEAD='5808659c59e3b981db0b9bd217886210932a5421'
CLOCK='2026-11-11T15:50:05.791502+00:00'
SUMMARY_SHA='f322f149d3ab7291f06f95af3869f5c66bfeca751f7e02477026c2c420a15e18'
FIXTURES={'paired_cohort_audit.py':'959cc8f0d43cadc55979904202202b2d096715a6ae4bee052496c76fb62d8f65',
          'p0_operational_audit.py':'b2adcb8c2ff54f9f66e7ab2c67d6c7bf7c0d7c6bf0a46be9aad0e2eb12363202'}
POST_FWD=('data/stage5/forward_validation/latest.json',
          'data/stage5/forward_validation/remote_witnesses.csv',
          'data/stage5/forward_validation/verified_run_metadata.csv',
          'data/stage5/forward_validation_v2/latest.json')


def sha(body):return hashlib.sha256(body).hexdigest()


def git(root,*args):
    return subprocess.run(['git','-C',str(root),*args],check=True,capture_output=True,
        env=dict(os.environ,GIT_NO_LAZY_FETCH='1',GIT_OPTIONAL_LOCKS='0'),timeout=240).stdout


def fingerprint(root):
    paths=[]
    for raw in git(root,'ls-files','-z').split(b'\0'):
        if not raw:continue
        path=os.fsdecode(raw)
        if path.endswith('.py') or path.startswith(('config/','system/')):paths.append(path)
    paths+=list(POST_FWD)+['data/direction_v42/forward_registry.csv',
        'data/direction_v42/forward_scores.csv','data/quote_evidence_v2/bindings.csv',
        'data/paired_forward_v1/pairs.jsonl','data/hourly_research/ohlc.csv',
        'data/p0_operations/latest.json']
    return {'head':git(root,'rev-parse','HEAD').decode().strip(),
        'refs':sha(git(root,'for-each-ref','--format=%(refname) %(objectname)')),
        'files':{p:sha((root/p).read_bytes()) for p in paths}}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--artifact-dir',type=Path,required=True)
    parser.add_argument('--clone',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--script',choices=tuple(FIXTURES),default='paired_cohort_audit.py')
    parser.add_argument('--prepare-only',action='store_true')
    args=parser.parse_args()
    source=args.source.resolve();root=args.clone.resolve();out=args.output.resolve()
    assert source!=root and not root.exists() and not out.is_relative_to(root)
    assert git(source,'rev-parse','HEAD').decode().strip()==HEAD
    assert not (source/'.git/objects/info/alternates').exists()
    assert not any((source/'.git/objects/pack').glob('*.promisor'))
    artifact=args.artifact_dir.resolve()
    if not (artifact/'summary.json').is_file():artifact=artifact/'source-audit-native-profile-43051436679-1'
    summary_bytes=(artifact/'summary.json').read_bytes();assert sha(summary_bytes)==SUMMARY_SHA
    summary=json.loads(summary_bytes)
    assert summary['source_head']==HEAD and summary['actual_captured_clock_utc']==CLOCK
    native=summary['trials'][1]['outputs']
    out.mkdir(parents=True,exist_ok=False)
    subprocess.run(['git','clone','--shared','--no-checkout',str(source),str(root)],
        check=True,capture_output=True,env=dict(os.environ,GIT_NO_LAZY_FETCH='1'),timeout=240)
    git(root,'config','core.autocrlf','false');git(root,'config','core.longpaths','true')
    git(root,'config','core.sparseCheckout','true');git(root,'config','core.sparseCheckoutCone','false')
    git(root,'config','index.sparse','false')
    (root/'.git/info/sparse-checkout').write_text('/*\n!/data/plus_evidence_v1_0/sources/\n',encoding='utf-8')
    git(root,'checkout','--detach',HEAD)
    overlays=list(POST_FWD)
    if args.script=='paired_cohort_audit.py':overlays.append('data/p0_operations/latest.json')
    for relative in overlays:
        body=(artifact/'native/outputs'/relative).read_bytes();assert sha(body)==native[relative]['sha256']
        target=root/relative;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(body)
    fixture=out/'captured-get'/(args.script+'.json');fixture.parent.mkdir()
    fixture_body=(artifact/'captured-get'/(args.script+'.json')).read_bytes()
    assert sha(fixture_body)==FIXTURES[args.script];fixture.write_bytes(fixture_body)
    expected_p0=(artifact/'native/outputs/data/p0_operations/latest.json').read_bytes()
    assert sha(expected_p0)==native['data/p0_operations/latest.json']['sha256']
    (out/'expected-p0.json').write_bytes(expected_p0)
    before=fingerprint(root)
    prepared={'scope':'DIAGNOSTIC_ONLY_NOT_THE_140_GATE','source_head':HEAD,'clock':CLOCK,
        'script':args.script,'clone':str(root),'fixture':str(fixture),'before':before,
        'source_artifact_id':13870237561,'source_summary_sha256':SUMMARY_SHA,
        'source_artifact_zip_sha256':'85a0f63965807cf83084cd1e65befb843e019d0c50feb060d82f7c564b206957',
        'overlaid_paths':overlays,'formal_performance_gate_evaluated':False}
    (out/'prepared.json').write_text(json.dumps(prepared,indent=2)+'\n',encoding='utf-8')
    if args.prepare_only:
        print(json.dumps({'status':'PREPARED_DIAGNOSTIC_ONLY','clone':str(root),'output':str(out)}));return 0
    assert args.script=='paired_cohort_audit.py'
    assert json.loads(fixture_body)['entries']==[]
    env=dict(os.environ,GIT_NO_LAZY_FETCH='1',GIT_OPTIONAL_LOCKS='0',GIT_TERMINAL_PROMPT='0',
        DOGE_AUDIT_STREAM_TRUSTED_OBJECT_DIRECTORY=str(source/'.git/objects'),
        DOGE_AUDIT_PAIRED_CPU_PROFILE='1',DOGE_AUDIT_PAIRED_COMPONENT_PROFILE='1',
        OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1',PYTHONHASHSEED='0',PYTHONUTF8='1',PYTHONDONTWRITEBYTECODE='1')
    env.pop('GITHUB_TOKEN',None)
    command=[sys.executable,'-I','-B',str(root/'runtime_audit_pipeline_profile.py'),'--child',
        '--source-root',str(root),'--script',args.script,'--fixture',str(fixture),'--clock',CLOCK,'--optimized']
    sys.path.insert(0,str(root));import runtime_process
    process=None;cleanup=None;error=None;started=time.perf_counter()
    with (out/'stdout.txt').open('xb') as stdout,(out/'stderr.txt').open('xb') as stderr:
        try:
            process=subprocess.Popen(command,cwd=root,env=env,stdout=stdout,stderr=stderr,start_new_session=True)
            code=process.wait(timeout=600)
        except BaseException as exc:error={'type':type(exc).__name__,'message':str(exc)[:1000]};code=None
        finally:
            if process is not None:cleanup=runtime_process.terminate_group(process)
    import runtime_audit_pipeline_profile as profile
    same_source=before==fingerprint(root)
    stdout_equal=(out/'stdout.txt').read_bytes()==(artifact/'native-paired_cohort_audit.py.stdout').read_bytes()
    comparisons={}
    for relative in ('data/paired_forward_v1/audit_latest.json','reports/DOGE_PAIRED_SELECTIVE_SHADOW_V1.md'):
        target=out/'outputs'/relative
        if not (root/relative).is_file():
            comparisons[relative]=False
            continue
        actual=(root/relative).read_bytes();expected=(artifact/'native/outputs'/relative).read_bytes()
        target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(actual)
        comparisons[relative]=profile.comparison_digest(actual,relative)==profile.comparison_digest(expected,relative)
    native_error=None;diagnostics=[]
    try:
        diagnostics=profile.validate_native_diagnostics((out/'stderr.txt').read_text(encoding='utf-8'),args.script,
            expected_launcher_sha256=sha((root/'runtime_audit_cli.py').read_bytes()),
            expected_stream_sha256=sha((root/'runtime_paired_git_stream.py').read_bytes()))
    except Exception as exc:native_error=type(exc).__name__+': '+str(exc)
    pstats_path=out/'native-paired.cprofile'
    if pstats_path.is_file():
        with (out/'pstats-summary.txt').open('x',encoding='utf-8') as stream:
            stats=pstats.Stats(str(pstats_path),stream=stream)
            stats.sort_stats('cumulative').print_stats(100);stats.sort_stats('tottime').print_stats(100)
    ok=code==0 and same_source and stdout_equal and all(comparisons.values()) and native_error is None
    report={**prepared,'status':'DIAGNOSTIC_EQUIVALENCE_OK' if ok else 'DIAGNOSTIC_FAILED',
        'instrumented_seconds':time.perf_counter()-started,'exit_code':code,'error':error,
        'cleanup':cleanup,'protected_bytes_unchanged':same_source,'stdout_exact':stdout_equal,
        'all_original_output_comparisons':comparisons,'native_diagnostics':diagnostics,'native_error':native_error,
        'formal_performance_gate_evaluated':False}
    (out/'diagnostic.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:report[k] for k in ('status','instrumented_seconds','exit_code','protected_bytes_unchanged',
        'stdout_exact','all_original_output_comparisons','native_error','formal_performance_gate_evaluated')}))
    return 0 if ok else 1


if __name__=='__main__':raise SystemExit(main())
