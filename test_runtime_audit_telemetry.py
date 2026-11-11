"""Bounded audit telemetry without Git fixtures or changes to command semantics."""
import ast
import builtins
import contextlib
import io
import json
import subprocess
from types import SimpleNamespace
import unittest
from pathlib import Path
from unittest.mock import patch
import runtime_audit_worker as worker

ORIGINAL_TIMED = '''
def timed_command(phase, *args, cwd, **metadata):
    start = time.monotonic()
    emit_progress(phase, 'begin', **metadata)
    try:
        output = command(*args, cwd=cwd)
    except BaseException as exc:
        try: exc._audit_command_phase = phase
        except Exception: pass
        emit_progress(phase, 'end', status='error', seconds=elapsed(start), error=exc, **metadata)
        raise
    seconds = time.monotonic()-start
    emit_progress(phase, 'end', status='ok', seconds=seconds, **metadata)
    return output, seconds
'''
CPU_KEYS=('parent_user_seconds','parent_system_seconds','child_user_seconds','child_system_seconds')
COUNTER_KEYS=('child_ru_inblock','child_ru_majflt','child_ru_nvcsw','child_ru_nivcsw')
def sample(values=(1.,2.,3.,4.), load=(1.,2.,3.), openblas='2', omp='1'):
    return dict(zip(CPU_KEYS,values), load=load, openblas_num_threads=openblas, omp_num_threads=omp)
class TelemetryTests(unittest.TestCase):
    def isolated_import(self,error):
        original_import=builtins.__import__
        def import_probe(name,*args,**kwargs):
            if name=='resource':raise error
            return original_import(name,*args,**kwargs)
        namespace={'__name__':'_safe_telemetry_import_probe','__file__':worker.__file__}
        with patch.object(builtins,'__import__',side_effect=import_probe):
            exec(compile(Path(worker.__file__).read_text(encoding='utf-8'),worker.__file__,'exec'),namespace)
        return namespace

    def test_optional_resource_loader_errors_cannot_block_worker_import(self):
        for error in (OSError('optional loader unavailable'),SystemError('optional module unavailable')):
            with self.subTest(kind=type(error).__name__):
                try:namespace=self.isolated_import(error)
                except Exception as caught:self.fail('optional diagnostic import blocked worker: '+type(caught).__name__)
                self.assertIsNone(namespace['_resource'])
                self.assertTrue(callable(namespace['timed_command']))

    def test_resource_import_preserves_cancellation(self):
        error=KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt) as caught:self.isolated_import(error)
        self.assertIs(caught.exception,error)

    def run_fake(self, snapshots, error=None):
        stream=io.StringIO(); result=object(); seen=[]
        def command(*args,**kwargs):
            seen.append((args,kwargs))
            if error is not None:raise error
            return result
        with patch.object(worker,'_read_command_telemetry',side_effect=snapshots,create=True), patch.object(worker,'command',side_effect=command), contextlib.redirect_stdout(stream):
            if error is None:
                output,seconds=worker.timed_command('p0_operational_audit.py','opaque-argument',cwd=Path('.'),input_head='a'*40)
            else:
                with self.assertRaises(type(error)) as raised:
                    worker.timed_command('p0_operational_audit.py','opaque-argument',cwd=Path('.'),input_head='a'*40)
                self.assertIs(raised.exception,error)
                output,seconds=None,None
        rows=[json.loads(line) for line in stream.getvalue().splitlines()]
        telemetry=[row for row in rows if row['kind']=='runtime_source_audit_telemetry']
        self.assertEqual(seen,[(('opaque-argument',),{'cwd':Path('.')})])
        self.assertEqual(len(telemetry),1,'missing bounded command telemetry')
        return result,output,seconds,rows,telemetry

    def test_cpu_deltas_and_bounded_environment_are_emitted(self):
        expected,output,seconds,rows,telemetry=self.run_fake([sample(),sample((2.,4.,6.,8.))])
        self.assertIs(output,expected);self.assertGreaterEqual(seconds,0)
        self.assertEqual(len(telemetry),1)
        row=telemetry[0]
        self.assertEqual({key:row[key+'_delta'] for key in CPU_KEYS},dict(zip(CPU_KEYS,(1.,2.,3.,4.))))
        self.assertEqual(row['openblas_num_threads'],'2');self.assertEqual(row['omp_num_threads'],'1')
        self.assertEqual(row['input_head'],'a'*40)
        self.assertEqual(row['load_1m_before'],1.)
        self.assertEqual(row['load_1m_after'],1.)
        self.assertEqual(row['cpu_status'],'ok')

    def test_diagnostic_read_failure_preserves_original_return(self):
        expected,output,seconds,rows,telemetry=self.run_fake([OSError('private diagnostic failure'),OSError('private diagnostic failure')])
        self.assertIs(output,expected)
        self.assertEqual(telemetry[0]['cpu_status'],'unknown')
        self.assertNotIn('private diagnostic failure',json.dumps(rows))

    def test_diagnostic_read_failure_preserves_calledprocesserror_identity(self):
        error=subprocess.CalledProcessError(7,['private-command'],output='private output',stderr='private stderr')
        self.run_fake([OSError('read'),OSError('read')],error)
        self.assertEqual(error._audit_command_phase,'p0_operational_audit.py')

    def test_diagnostic_read_failure_preserves_timeout_identity(self):
        error=subprocess.TimeoutExpired(['private-command'],240,output='private output')
        self.run_fake([OSError('read'),OSError('read')],error)
        self.assertEqual(error.timeout,240)

    def test_original_baseexception_is_reraised(self):
        self.run_fake([sample(),sample()],KeyboardInterrupt())

    def test_bad_numeric_values_and_private_environment_are_omitted(self):
        before=sample((float('nan'),2.,3.,4.),load=(float('inf'),2.,3.),openblas='secret-token',omp='0')
        after=sample((float('inf'),3.,4.,5.),load=(1.,float('nan'),3.),openblas='secret-token',omp='0')
        _,_,_,rows,telemetry=self.run_fake([before,after])
        row=telemetry[0]
        self.assertEqual(row['cpu_status'],'unknown')
        self.assertFalse(any(key.endswith('_delta') or key.startswith('load_') for key in row))
        self.assertEqual(row['openblas_num_threads'],'unknown');self.assertEqual(row['omp_num_threads'],'unknown')
        encoded=json.dumps(rows,allow_nan=False)
        self.assertNotIn('secret-token',encoded);self.assertNotIn('private-',encoded)

    def test_decreasing_cpu_counters_are_not_reported_as_valid_deltas(self):
        _,_,_,_,telemetry=self.run_fake([sample((4.,3.,2.,1.)),sample((3.,2.,1.,0.))])
        self.assertEqual(telemetry[0]['cpu_status'],'unknown')
        self.assertFalse(any(key.endswith('_delta') for key in telemetry[0]))

    def test_emission_failure_cannot_replace_command_result_or_exception(self):
        value=object();error=subprocess.CalledProcessError(3,['original'])
        with patch.object(worker,'_read_command_telemetry',return_value=sample(),create=True), patch.object(worker,'command',return_value=value), patch.object(worker.json,'dumps',side_effect=OSError('cannot emit')):
            self.assertIs(worker.timed_command('clone','arg',cwd=Path('.'))[0],value)
        with patch.object(worker,'_read_command_telemetry',return_value=sample(),create=True), patch.object(worker,'command',side_effect=error), patch.object(worker.json,'dumps',side_effect=OSError('cannot emit')):
            with self.assertRaises(subprocess.CalledProcessError) as raised:worker.timed_command('clone','arg',cwd=Path('.'))
            self.assertIs(raised.exception,error)

    def test_resource_unavailable_is_unknown_and_no_sampling_process_is_started(self):
        self.assertTrue(callable(getattr(worker,'_read_command_telemetry',None)),'missing bounded reader')
        with patch.object(worker,'_resource',None), patch.object(worker.os,'getloadavg',side_effect=OSError('unsupported'),create=True), patch.object(worker.os,'environ',{'OPENBLAS_NUM_THREADS':'0002','OMP_NUM_THREADS':'9999999'}), patch.object(worker.subprocess,'run',side_effect=AssertionError('no sampling command')):
            row=worker._read_command_telemetry()
        self.assertNotIn('child_user_seconds',row)
        self.assertNotIn('load',row)
        self.assertEqual(row['openblas_num_threads'],'unknown');self.assertEqual(row['omp_num_threads'],'unknown')

    def test_thread_values_accept_only_short_positive_decimal_strings(self):
        self.assertTrue(callable(getattr(worker,'_read_command_telemetry',None)),'missing bounded reader')
        with patch.object(worker.os,'environ',{'OPENBLAS_NUM_THREADS':'12','OMP_NUM_THREADS':'4'}):
            row=worker._read_command_telemetry()
        self.assertEqual(row['openblas_num_threads'],'12');self.assertEqual(row['omp_num_threads'],'4')
        with patch.object(worker.os,'environ',{'OPENBLAS_NUM_THREADS':' 1','OMP_NUM_THREADS':'1\n'}):
            row=worker._read_command_telemetry()
        self.assertEqual(row['openblas_num_threads'],'unknown');self.assertEqual(row['omp_num_threads'],'unknown')

    def test_supported_resource_reads_self_and_waited_children_exactly_once(self):
        seen=[]
        def usage(kind):
            seen.append(kind)
            return SimpleNamespace(ru_utime=1. if kind==11 else 3.,ru_stime=2. if kind==11 else 4.)
        resource=SimpleNamespace(RUSAGE_SELF=11,RUSAGE_CHILDREN=22,getrusage=usage)
        with patch.object(worker,'_resource',resource):row=worker._read_command_telemetry()
        self.assertEqual(seen,[11,22])
        self.assertEqual({key:row[key] for key in CPU_KEYS},dict(zip(CPU_KEYS,(1.,2.,3.,4.))))

    def test_untrusted_snapshot_members_never_enter_the_output_schema(self):
        before=sample();after=sample((2.,3.,4.,5.))
        before['private_url']='https://secret.invalid/token';after['argv']=['private-command']
        _,_,_,rows,telemetry=self.run_fake([before,after])
        serialized=json.dumps(rows,allow_nan=False)
        self.assertNotIn('secret.invalid',serialized);self.assertNotIn('private-command',serialized)
        self.assertNotIn('private_url',telemetry[0]);self.assertNotIn('argv',telemetry[0])

    def test_child_raw_counter_deltas_are_independent_optional_integers(self):
        before=sample();after=sample((2.,3.,4.,5.))
        before.update(zip(COUNTER_KEYS,(10,20,30,40)));after.update(zip(COUNTER_KEYS,(12,23,34,45)))
        _,_,_,_,rows=self.run_fake([before,after]);row=rows[0]
        self.assertIn('child_ru_inblock_delta',row,'missing original resource counters')
        self.assertEqual({key:row[key+'_delta'] for key in COUNTER_KEYS},dict(zip(COUNTER_KEYS,(2,3,4,5))))
        self.assertEqual(row['cpu_status'],'ok')

    def test_cpu_count_and_affinity_count_keep_their_original_meaning(self):
        before=sample();after=sample((2.,3.,4.,5.))
        before.update(os_cpu_count=4,sched_getaffinity_count=2)
        after.update(os_cpu_count=8,sched_getaffinity_count=4)
        _,_,_,_,rows=self.run_fake([before,after]);row=rows[0]
        self.assertEqual(row.get('os_cpu_count_before'),4,'missing system capacity')
        self.assertEqual(row.get('os_cpu_count_after'),8)
        self.assertEqual(row.get('sched_getaffinity_count_before'),2)
        self.assertEqual(row.get('sched_getaffinity_count_after'),4)

    def test_missing_bad_or_decreasing_optional_counts_preserve_cpu(self):
        before=sample();after=sample((2.,3.,4.,5.))
        before.update(child_ru_inblock=10,child_ru_nvcsw=True,child_ru_nivcsw=5,os_cpu_count=True,sched_getaffinity_count=0)
        after.update(child_ru_inblock=12,child_ru_majflt=3,child_ru_nvcsw=4,child_ru_nivcsw=4,os_cpu_count=0,sched_getaffinity_count=2.5)
        _,_,_,_,rows=self.run_fake([before,after]);row=rows[0]
        self.assertEqual(row.get('child_ru_inblock_delta'),2,'valid counter lost')
        self.assertEqual(row['cpu_status'],'ok')
        for key in ('child_ru_majflt_delta','child_ru_nvcsw_delta','child_ru_nivcsw_delta','os_cpu_count_before','os_cpu_count_after','sched_getaffinity_count_before','sched_getaffinity_count_after'):
            self.assertNotIn(key,row)

    def test_resource_snapshot_reads_optional_counters_and_capacity(self):
        def usage(kind):
            return SimpleNamespace(ru_utime=1.,ru_stime=2.,ru_inblock=7,ru_majflt=8,ru_nvcsw=9,ru_nivcsw=10)
        resource=SimpleNamespace(RUSAGE_SELF=11,RUSAGE_CHILDREN=22,getrusage=usage)
        with patch.object(worker,'_resource',resource),patch.object(worker.os,'cpu_count',return_value=4),patch.object(worker.os,'sched_getaffinity',return_value={0,2},create=True):
            row=worker._read_command_telemetry()
        self.assertEqual(row.get('child_ru_inblock'),7,'missing original resource counter')
        self.assertEqual({key:row[key] for key in COUNTER_KEYS},dict(zip(COUNTER_KEYS,(7,8,9,10))))
        self.assertEqual(row.get('os_cpu_count'),4)
        self.assertEqual(row.get('sched_getaffinity_count'),2)

    def test_optional_capacity_failure_and_missing_resource_fields_do_not_drop_cpu(self):
        def usage(kind):return SimpleNamespace(ru_utime=1.,ru_stime=2.,ru_inblock=False,ru_majflt=-1,ru_nvcsw=1.5)
        resource=SimpleNamespace(RUSAGE_SELF=11,RUSAGE_CHILDREN=22,getrusage=usage)
        with patch.object(worker,'_resource',resource),patch.object(worker.os,'cpu_count',side_effect=OSError('unavailable')),patch.object(worker.os,'sched_getaffinity',side_effect=OSError('unavailable'),create=True):
            row=worker._read_command_telemetry()
        self.assertEqual(row['child_user_seconds'],1.)
        for key in COUNTER_KEYS+('os_cpu_count','sched_getaffinity_count'):self.assertNotIn(key,row)

    def test_business_ast_is_unchanged_after_removing_telemetry_only_nodes(self):
        tree=ast.parse(Path(worker.__file__).read_text(encoding='utf-8'))
        actual=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='timed_command')
        class RemoveTelemetry(ast.NodeTransformer):
            def visit_Assign(self,node):
                if isinstance(node.value,ast.Call) and isinstance(node.value.func,ast.Name) and node.value.func.id=='_safe_command_telemetry':return None
                return self.generic_visit(node)
            def visit_Expr(self,node):
                if isinstance(node.value,ast.Call) and isinstance(node.value.func,ast.Name) and node.value.func.id=='_emit_command_telemetry':return None
                return self.generic_visit(node)
        cleaned=RemoveTelemetry().visit(actual)
        expected=ast.parse(ORIGINAL_TIMED).body[0]
        self.assertEqual(ast.dump(cleaned,include_attributes=False),ast.dump(expected,include_attributes=False))
if __name__=='__main__':unittest.main()

