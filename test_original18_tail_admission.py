"""Owned admission only: actual pure frozen plan/deadline, no producer/model/HTTP."""
import ast
from contextlib import contextmanager
import copy, importlib.util, json, math, os, sys, types, unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

HERE=Path(__file__).resolve().parent
PROJECT=next((p for p in HERE.parents if (p/'four-fail-repair-20261109').is_dir()),None)

def source(name):
    paths=[HERE/name]
    if PROJECT:
        paths += [PROJECT/'four-fail-repair-20261109/readiness'/name,
            PROJECT/'continuation-20261110/original18-tail-gap-diagnosis'/name,
            PROJECT/'continuation-20261110/integration'/name]
    return next(p for p in paths if p.is_file())

def functions(name,names,namespace):
    path=source(name);tree=ast.parse(path.read_bytes(),filename=str(path))
    nodes=[node for node in tree.body if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)) and node.name in names]
    assert {node.name for node in nodes}==set(names)
    exec(compile(ast.Module(body=nodes,type_ignores=[]),str(path),'exec'),namespace)
    return namespace

# Compile the unchanged scientific function bodies from their actual sources.
# No scientific issuance, quote, score or audit is executed in these tests.
dt={'datetime':datetime,'timedelta':timedelta,'timezone':timezone}
evaluation=types.SimpleNamespace(**functions('factor_evaluation_v1.py',['utc'],dict(dt)))
research_tree=ast.parse(source('phase1_research.py').read_bytes())
groups=next(node.value for node in research_tree.body if isinstance(node,ast.Assign)
            and any(isinstance(t,ast.Name) and t.id=='GROUPS' for t in node.targets))
research=types.SimpleNamespace(**functions('phase1_research.py',['variants'],{'GROUPS':ast.literal_eval(groups)}))
shadow=types.SimpleNamespace(**functions('learned_shadow_v1.py',['utc','check_policy','target_deadline','due_slots'],
    dict(dt,evaluation=evaluation,research=research,math=math)))
original=types.SimpleNamespace(**functions('learned_shadow_runtime.py',['plan'],dict(dt,shadow=shadow)))

def load_guard(path=HERE/'p0_open_target_guard.py'):
    diagnostics=types.ModuleType('forward_diagnostics');diagnostics.ROOT=HERE
    diagnostics.SOURCES={'production_registry':'registry.csv','production_scores':'scores.csv'}
    diagnostics.read_csv=lambda *a,**kw:[]
    retry=types.ModuleType('quote_capture_retry')
    spec=importlib.util.spec_from_file_location('original18_tail_guard_subject',path)
    module=importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules,{'forward_diagnostics':diagnostics,'quote_capture_retry':retry}):spec.loader.exec_module(module)
    return module

g=load_guard()

class Admission(unittest.TestCase):
    def setUp(self):
        self.now=datetime(2026,11,10,7,12,tzinfo=timezone.utc)
        self.after=self.now
        self.ended=datetime(2026,11,10,7,11,57,tzinfo=timezone.utc)
        self.root=HERE/'READ_ONLY_ROOT_NOT_CREATED'
        self.policy={'effective_from_utc':'2026-10-16T00:00:00+00:00','horizons_hours':[1,24],
            'slot_steps_hours':{'1':2,'24':25},'target_window_minutes':25,'max_issue_delay_minutes':45,
            'groups':list(research.variants()),'family_size':1080,'automatic_promotion':False,
            'fee_per_side':.001,'slippage_per_side':.0005,'notional_usdt':100,'stress_multiplier':2}
        self.state=self.current_state(self.now)
        event=self.state['events'][0]
        event['issued_at_utc']='2026-11-10T06:13:40+00:00'
        event['target_utc']='2026-11-10T07:13:40+00:00'
        self.key=event['event_id'];self.state['scores'].pop(self.key,None)
        self.prior={'status':'CAPTURE_LOOP_COMPLETED','fatal':False,'ended_at_utc':self.ended.isoformat()}
        self.calls=[]
        self.env={'DOGE_TAIL_CAPTURE_BRIDGE':'1','DOGE_PROSPECTIVE_RUNTIME_BRIDGE':'1','GITHUB_ACTIONS':'true',
            'GITHUB_REF':'refs/heads/main','GITHUB_REPOSITORY':'lzwislam-collab/doge-data-collector'}
        self.runtime=types.ModuleType('runtime_deadline_service');self.runtime.utc=evaluation.utc
        test=self
        class LiveAdapter:
            def __init__(self,*a,**kw):raise AssertionError('writer adapter must not be constructed')
            @staticmethod
            def _prospective_env(**extra):return dict(os.environ,**extra)
            def original18_state_owned(consumer):
                test.calls.append(consumer)
                assert type(consumer) is types.SimpleNamespace
                assert consumer.root==test.root
                assert consumer._prospective_env is LiveAdapter._prospective_env
                assert test.scope_active
                if test.failure:raise test.failure
                return copy.deepcopy(test.state)
        self.runtime.LiveAdapter=LiveAdapter
        self.bridge=types.ModuleType('runtime_forward_bridge');self.bridge.original=original
        self.process=types.ModuleType('runtime_process')
        class RuntimeCancelled(BaseException):pass
        self.process.RuntimeCancelled=RuntimeCancelled
        self.scope_active=False;self.scope_count=0;self.failure=None
        @contextmanager
        def cancellation_signals():
            test.scope_count+=1;test.scope_active=True
            try:yield
            finally:test.scope_active=False
        self.process.cancellation_signals=cancellation_signals
        class ActualClock(datetime):
            @classmethod
            def now(cls,tz=None):return test.after
        self.clock=ActualClock

    def current_state(self,at):
        start=evaluation.utc(self.policy['effective_from_utc']);events=[];scores={};bindings={}
        for h in (1,24):
            step=timedelta(hours=self.policy['slot_steps_hours'][str(h)])
            slot=start+((at-start)//step)*step;key=str(int(slot.timestamp()))+'_'+str(h)
            target=slot+timedelta(hours=h,minutes=2)
            events.append({'event_id':key,'slot_utc':slot.isoformat(),'issued_at_utc':(slot+timedelta(minutes=2)).isoformat(),
                'target_utc':target.isoformat(),'horizon_hours':h})
            bindings[key+'_prediction']=True
            if target<=at:scores[key]=True;bindings[key+'_target']=True
        return {'policy':copy.deepcopy(self.policy),'events':events,'scores':scores,'bindings':bindings}

    def call(self):
        def read_csv(path,**kwargs):
            if Path(path).name=='scores.csv':return []
            return [{'forecast_id':'V42_7Z','horizon_hours':'1','source_hour_start_utc':self.now.replace(minute=0,second=0).isoformat(),
                     'target_utc':(self.now+timedelta(hours=1)).isoformat()}]
        with patch.dict(os.environ,self.env),patch.dict(sys.modules,{'runtime_deadline_service':self.runtime,
                'runtime_forward_bridge':self.bridge,'runtime_process':self.process}),\
                patch.object(g.Path,'exists',return_value=True),patch.object(g.Path,'read_text',return_value=json.dumps(self.prior)),\
                patch.object(g.diagnostics,'read_csv',side_effect=read_csv),patch.object(g,'datetime',self.clock):
            return g.needs_tail_bridge(self.root,self.now)

    def test_actual_6z_event_future_target_admits_with_complete_v42(self):
        self.assertEqual(self.key,'1794290400_1')
        self.assertTrue(self.call());self.assertEqual(len(self.calls),1)

    def test_original_current_issuance_plan_admits(self):
        self.now=self.after=datetime(2026,11,10,8,10,tzinfo=timezone.utc);self.ended=self.now-timedelta(seconds=10)
        self.prior['ended_at_utc']=self.ended.isoformat();self.state=self.current_state(self.now)
        removed=next(e['event_id'] for e in self.state['events'] if e['horizon_hours']==1)
        self.state['events']=[e for e in self.state['events'] if e['event_id']!=removed]
        self.state['scores'].pop(removed,None)
        self.state['bindings'].pop(removed+'_prediction',None);self.state['bindings'].pop(removed+'_target',None)
        self.assertEqual(original.plan(self.policy,self.state['events'],self.state['scores'],self.now)['issue'],[1])
        self.assertTrue(self.call())

    def test_original_due_score_plan_admits(self):
        self.now=self.after=datetime(2026,11,10,7,14,tzinfo=timezone.utc)
        self.prior['ended_at_utc']=(self.now-timedelta(seconds=10)).isoformat()
        self.assertIn(self.key,original.plan(self.policy,self.state['events'],self.state['scores'],self.now)['score'])
        self.assertTrue(self.call())

    def test_far_future_fully_predicted_event_does_not_admit(self):
        self.state['events'][0]['target_utc']=(self.now+timedelta(minutes=5,seconds=1)).isoformat()
        self.assertFalse(self.call())

    def test_already_scored_and_bound_has_no_remaining_obligation(self):
        self.state['scores'][self.key]=True;self.state['bindings'][self.key+'_target']=True
        self.assertFalse(self.call())

    def test_prediction_binding_or_scored_target_binding_pending_before_original_deadline(self):
        self.state['events'][0]['target_utc']=(self.now+timedelta(minutes=10)).isoformat()
        self.state['bindings'].pop(self.key+'_prediction')
        self.assertTrue(self.call())
        self.state['bindings'][self.key+'_prediction']=True;self.state['scores'][self.key]=True
        self.state['bindings'].pop(self.key+'_target',None)
        self.assertTrue(self.call())

    def test_strict_next_slot_deadline_not_naive_target_plus_25(self):
        self.now=self.after=datetime(2026,11,10,2,5,tzinfo=timezone.utc)
        self.prior['ended_at_utc']=(self.now-timedelta(seconds=10)).isoformat();self.state=self.current_state(self.now)
        e={'event_id':'1794268800_1','slot_utc':'2026-11-10T00:00:00+00:00','issued_at_utc':'2026-11-10T00:44:00+00:00',
           'target_utc':'2026-11-10T01:44:00+00:00','horizon_hours':1}
        self.state['events'].append(e)
        self.assertEqual(shadow.target_deadline(e,self.policy).hour,2)
        self.assertLess(self.now,evaluation.utc(e['target_utc'])+timedelta(minutes=25))
        self.assertFalse(self.call())

    def test_expired_target_and_pending_bindings_do_not_admit(self):
        self.now=self.after=datetime(2026,11,10,7,38,40,tzinfo=timezone.utc)
        self.prior['ended_at_utc']=(self.now-timedelta(seconds=10)).isoformat()
        self.state['bindings'].pop(self.key+'_prediction')
        self.assertFalse(self.call())

    def test_dependencies_disabled_does_not_read_original18(self):
        self.env['DOGE_PROSPECTIVE_RUNTIME_BRIDGE']='0'
        self.assertFalse(self.call());self.assertFalse(self.calls)

    def test_original_environment_and_prior_gates_do_not_read(self):
        for name,value in [('GITHUB_REF','refs/heads/other'),('GITHUB_ACTIONS','false'),('DOGE_TAIL_CAPTURE_BRIDGE','0')]:
            with self.subTest(name=name):
                prior=self.env[name];self.env[name]=value
                self.assertFalse(self.call());self.assertFalse(self.calls);self.env[name]=prior
        for change in ({'status':'RUNNING'},{'fatal':True},{'ended_at_utc':(self.now-timedelta(seconds=181)).isoformat()}):
            with self.subTest(change=change):
                prior=copy.deepcopy(self.prior);self.prior.update(change)
                self.assertFalse(self.call());self.assertFalse(self.calls);self.prior=prior

    def test_reader_completion_fresh_clock_rechecks_180_second_admission(self):
        self.after=self.ended+timedelta(seconds=181)
        self.assertFalse(self.call());self.assertEqual(len(self.calls),1)

    def test_reader_error_and_cancellation_propagate_with_scope_restored(self):
        for failure in (ValueError('strict journal invalid'),TimeoutError('owned60'),self.process.RuntimeCancelled()):
            with self.subTest(failure=type(failure).__name__):
                self.failure=failure
                with self.assertRaises(type(failure)) as got:self.call()
                self.assertIs(got.exception,failure);self.assertFalse(self.scope_active)

    def test_existing_v42_true_short_circuits_original_reader(self):
        with patch.dict(os.environ,self.env),patch.dict(sys.modules,{'runtime_deadline_service':self.runtime}),\
                patch.object(g.Path,'exists',return_value=True),patch.object(g.Path,'read_text',return_value=json.dumps(self.prior)),\
                patch.object(g.diagnostics,'read_csv',return_value=[]):
            self.assertTrue(g.needs_tail_bridge(self.root,self.now));self.assertFalse(self.calls)

    def test_readonly_consumer_never_constructs_adapter_or_auditor(self):
        self.assertTrue(self.call());self.assertTrue(self.calls)
        self.assertIs(type(self.calls[0]),types.SimpleNamespace);self.assertEqual(self.scope_count,1)

    def test_five_minute_future_and_180_second_freshness_boundaries_are_inclusive(self):
        self.state['events'][0]['target_utc']=(self.now+timedelta(minutes=5)).isoformat()
        self.prior['ended_at_utc']=(self.now-timedelta(seconds=180)).isoformat()
        self.assertTrue(self.call())
        self.after+=timedelta(microseconds=1)
        self.assertFalse(self.call())

    def test_original_plan_invalid_state_failure_is_not_empty_success(self):
        self.state['events'].append(copy.deepcopy(self.state['events'][0]))
        with self.assertRaisesRegex(ValueError,'duplicate event'):self.call()

    def test_existing_owned_reader_method_is_reused_with_60_second_cap_and_schema(self):
        # Exercise the actual unchanged owned method body with I/O seams only.
        # Its child code never executes, and no temp directory or writer exists.
        tree=ast.parse(source('runtime_deadline_service.py').read_bytes())
        adapter=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='LiveAdapter')
        method=next(n for n in adapter.body if isinstance(n,ast.FunctionDef) and n.name=='original18_state_owned')
        observed=[];raw=json.dumps(self.state).encode()
        process=types.SimpleNamespace(command=lambda *a,**kw:(observed.append((a,kw)) or 'ORIGINAL18_STATE_READY\n'))
        @contextmanager
        def folder(**kw):yield str(self.root/'OWNED_READ_NO_DIRECTORY_CREATED')
        namespace={'Path':Path,'tempfile':types.SimpleNamespace(TemporaryDirectory=folder),
            'runtime_process':process,'sys':sys,'json':json}
        exec(compile(ast.Module(body=[method],type_ignores=[]),str(source('runtime_deadline_service.py')),'exec'),namespace)
        original_reader=self.runtime.LiveAdapter.original18_state_owned
        with patch.object(self.runtime.LiveAdapter,'original18_state_owned',namespace['original18_state_owned']),\
                patch.object(Path,'is_symlink',return_value=False),patch.object(Path,'is_file',return_value=True),\
                patch.object(Path,'stat',return_value=types.SimpleNamespace(st_size=len(raw))),\
                patch.object(Path,'read_bytes',return_value=raw):
            self.assertTrue(self.call())
        self.assertEqual(len(observed),1);args,kwargs=observed[0]
        self.assertEqual(kwargs['timeout'],60);self.assertEqual(kwargs['cwd'],self.root)
        self.assertIn('from runtime_forward_bridge import original18_state',args[0][2])
        self.assertIs(self.runtime.LiveAdapter.original18_state_owned,original_reader)

if __name__=='__main__':unittest.main(verbosity=2)
