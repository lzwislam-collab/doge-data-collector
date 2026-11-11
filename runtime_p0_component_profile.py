"""P0 diagnostic: original functions/hooks, complete recorded GETs and UTC."""
import argparse
from collections import defaultdict
import datetime
import hashlib
import json
import os
from pathlib import Path
import runpy
import sys
import threading
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--snapshot', type=Path, required=True)
    parser.add_argument('--fixture', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--expected', type=Path, required=True)
    parser.add_argument('--clock', required=True)
    parser.add_argument('--source-head', default='5808659c59e3b981db0b9bd217886210932a5421')
    args = parser.parse_args()
    root = args.snapshot.resolve()
    sys.path.insert(0, str(root))
    import numpy  # match the original diagnostic's extension import order
    import runtime_audit_pipeline_profile as original_profile
    before = original_profile.fingerprint(root)
    producer_paths = ('data/p2_paired_execution_v3_1/books.jsonl',
        'data/stage5/forward_validation/remote_witnesses.csv',
        'data/stage5/forward_validation/verified_run_metadata.csv')
    producer_before = {p:hashlib.sha256((root/p).read_bytes()).hexdigest() for p in producer_paths}
    source_files = ('p0_source_binding.py','p0_operational_audit.py','runtime_audit_cli.py')
    source_hashes = {p:hashlib.sha256((root/p).read_bytes()).hexdigest() for p in source_files}
    at = datetime.datetime.fromisoformat(args.clock)
    active = {}
    first_proof = defaultdict(int)
    pending = {}
    stats = defaultdict(lambda: {'calls':0, 'inclusive_seconds':0.0,
                                'process_cpu_seconds':0.0,'children_cpu_seconds':0.0})
    details = []
    selected = {
        ('p0_source_binding.py','locked_contexts'),
        ('p0_source_binding.py','_first_proof'),
        ('p0_source_binding.py','recover_locked_publication'),
        ('runtime_p2_history.py','history_guard'),
        ('p2_paired_execution_v3_1.py','guard'),
        ('p2_paired_execution_v3_1.py','read_events'),
        ('p2_paired_execution_v3_1.py','history_guard'),
        ('runtime_paired_history.py','history_guard'),
        ('runtime_paired_history.py','_compatible'),
        ('paired_forward_cohort.py','guard'),
        ('paired_forward_cohort.py','history_guard'),
        ('runtime_publication_state.py','history_receipts'),
        ('forward_validation.py','remote_witnesses'),
        ('forward_validation.py','evaluate'),
        ('forward_validation.py','github_json'),
        ('p0_operational_audit.py','fetch_operational_runs'),
        ('runtime_p0_inventory_transport.py','fetch_operational_runs'),
    }

    def cpu():
        t = os.times()
        return time.process_time(), t.children_user+t.children_system

    def observe(frame, event, result):
        if event not in ('call','return'): return
        code = frame.f_code
        file = code.co_filename.replace('\\','/').rsplit('/',1)[-1]
        name = code.co_name
        tid = threading.get_ident()
        key = id(frame)
        if event == 'call':
            if file == 'p0_source_binding.py' and name == 'recover_locked_publication':
                active[tid] = frame.f_locals['row']['forecast_id']
            if file == 'p0_source_binding.py' and name == '_first_proof': first_proof[tid] += 1
            label = file+':'+name
            info = {}
            wanted = (file,name) in selected
            if file == '__init__.py' and name == 'loads' and first_proof[tid]:
                wanted = True; label = 'json.loads:inside_first_proof'
            if file == 'forward_validation.py' and name == 'git':
                argv = frame.f_locals.get('args', ())
                if '-S' in argv:
                    wanted = True; label = 'git:pickaxe_first_event'; info['argument_count'] = len(argv)
                elif len(argv) == 2 and argv[0] == 'show' and any(
                        argv[1].endswith(':'+p) for p in ('data/p0_operations/latest.json','data/p2_paired_execution_v3_1/books.jsonl')):
                    wanted = True; label = 'git:first_proof_show'; info['spec'] = argv[1]
            if file == 'publish_forward_evidence.py' and name == 'command':
                argv = frame.f_locals.get('args', ())
                if '--no-walk=unsorted' in argv:
                    wanted = True; label = 'git:native_history_batch'; info['path'] = argv[-1]
            if wanted:
                if name == 'history_receipts': info['relative'] = frame.f_locals.get('relative')
                if name == 'github_json': info['suffix'] = frame.f_locals.get('suffix')
                if name == 'remote_witnesses':
                    info.update(registry_rows=len(frame.f_locals['registry']),
                                prior_rows=len(frame.f_locals['prior']),runs=len(frame.f_locals['runs']))
                process_cpu,children_cpu = cpu()
                pending[key] = (time.perf_counter(),process_cpu,children_cpu,label,active.get(tid),info)
        elif key in pending:
            start,start_cpu,start_children,label,fid,info = pending.pop(key)
            seconds = time.perf_counter()-start
            process_cpu,children_cpu = cpu()
            own_cpu = max(0,process_cpu-start_cpu);child_cpu = max(0,children_cpu-start_children)
            group = label+('|'+str(info.get('relative')) if 'relative' in info else '')
            stats[group]['calls'] += 1; stats[group]['inclusive_seconds'] += seconds
            stats[group]['process_cpu_seconds'] += own_cpu; stats[group]['children_cpu_seconds'] += child_cpu
            if label != 'json.loads:inside_first_proof':
                item = dict(function=label, fid=fid, seconds=seconds, process_cpu_seconds=own_cpu,
                            children_cpu_seconds=child_cpu, **info)
                if name == '_compatible': item['compatible'] = result
                if label == 'git:first_proof_show' and isinstance(result,str): item['text_characters'] = len(result)
                details.append(item)
        if event == 'return':
            if file == 'p0_source_binding.py' and name == '_first_proof': first_proof[tid] -= 1
            if file == 'p0_source_binding.py' and name == 'recover_locked_publication': active.pop(tid,None)

    source_head = original_profile.git(root,'rev-parse','HEAD').strip().decode()
    if source_head != args.source_head: raise ValueError('diagnostic source HEAD differs from requested immutable snapshot')
    args.output.parent.mkdir(parents=True,exist_ok=True)
    report = {'scope':'Instrumented component attribution only; not the original140second acceptance',
              'source_head':source_head,'platform':sys.platform,
              'clock':args.clock,'source_hashes':source_hashes,'metadata_fixture':str(args.fixture),
              'metadata_fixture_sha256':hashlib.sha256(args.fixture.read_bytes()).hexdigest(),
              'original_functions_not_replaced':True,'all_metadata_consumption_enforced':True,
              'process_cpu_counters_include_threads':True,'children_cpu_available':os.name=='posix'}
    error = None
    started = time.perf_counter()
    old = sys.getprofile(); old_thread = threading.getprofile()
    try:
        with original_profile.transport('replay', args.fixture), original_profile.frozen_clock(at):
            import runtime_audit_cli as launcher
            namespace = runpy.run_path(str(root/'p0_operational_audit.py'),run_name='_doge_owned_audit_cli')
            globals_ = namespace['main'].__globals__
            with launcher._hooks(root,'p0_operational_audit.py'), \
                    launcher._p0_hook(root,'p0_operational_audit.py',globals_), \
                    launcher._paired_stream_hook(root,'p0_operational_audit.py',globals_), \
                    launcher._witness_stream_hook(root,'p0_operational_audit.py',globals_):
                sys.setprofile(observe); threading.setprofile(observe)
                value = namespace['evaluate'](root)
                sys.setprofile(old); threading.setprofile(old_thread)
                output = args.output.with_name(args.output.stem+'-result.json')
                output.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')
                actual = original_profile.comparison_digest(output.read_bytes(),'data/p0_operations/latest.json')
                expected = original_profile.comparison_digest(args.expected.read_bytes(),'data/p0_operations/latest.json')
                report.update(status='ok',eligible_count=len(value['forecast_proofs']),
                              rejected_count=len(value['rejected_evidence']),result_file=str(output),
                              result_semantic_sha256=actual,expected_semantic_sha256=expected,
                              result_equals_original_native=actual==expected)
                if actual != expected: raise original_profile.ProfileError('P0 diagnostic output differs from original native report')
    except BaseException as exc:
        error = exc
        report.update(status='failed',error_type=type(exc).__name__,error=str(exc)[:1500])
        stderr = getattr(exc,'stderr',None)
        if isinstance(stderr,bytes): report['original_error_stderr'] = stderr.decode('utf-8','replace')[:1500]
    finally:
        sys.setprofile(old); threading.setprofile(old_thread)
        report.update(evaluate_seconds=time.perf_counter()-started,stats=dict(stats),details=details,
                      scientific_inputs_unchanged=before==original_profile.fingerprint(root),
                      producer_inputs_unchanged=producer_before=={p:hashlib.sha256((root/p).read_bytes()).hexdigest() for p in producer_paths},
                      source_files_unchanged=source_hashes=={p:hashlib.sha256((root/p).read_bytes()).hexdigest() for p in source_files},
                      inclusive_times_overlap=True)
        if not all(report[key] for key in ('scientific_inputs_unchanged','producer_inputs_unchanged','source_files_unchanged')):
            if error is None: error = original_profile.ProfileError('diagnostic input/source mutation detected')
            report.update(status='failed',error_type=type(error).__name__,error=str(error)[:1500])
        args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print(json.dumps({k:report[k] for k in ('status','evaluate_seconds','scientific_inputs_unchanged','source_files_unchanged')}))
    if error is not None: raise error


if __name__ == '__main__': main()
