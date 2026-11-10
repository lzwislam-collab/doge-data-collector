"""Daily lock-time checkout contract; synthetic local Git only, all fixtures retained."""
from pathlib import Path
import hashlib, json, os, re, shutil, subprocess, tempfile, unittest, uuid

HERE=Path(__file__).resolve().parent
WORKFLOW=HERE/'.github/workflows/doge_history.yml'
NEW_REF="${{ github.ref == 'refs/heads/main' && 'main' || github.sha }}"
OLD_REF="${{ github.event_name == 'push' && startsWith(github.event.head_commit.message, 'data: clock recovery request daily ') && 'main' || github.sha }}"
SNAPSHOTS=('data/forecast_v41/backtest_latest.json','data/forecast_v41/calibration_research.json',
    'data/forecast_v41/latest.json','data/forecast_v41/scores.json',
    'data/history/latest.json','data/prospective/scores.json')
GIT=shutil.which('git') or r'C:/Users/lzwis/.cache/codex-runtimes/codex-primary-runtime/dependencies/native/git/cmd/git.exe'

def workflow_ref():
    text=WORKFLOW.read_text(encoding='utf-8')
    match=re.search(r"(?m)^      - uses: actions/checkout@v4\n        with:\n          fetch-depth: 0\n          ref: >-\n            (.+)$",text)
    if match is None:raise AssertionError('exact existing full-history checkout step required')
    return match[1]

def ref_for(expression,event,ref,sha,message=''):
    # Exact two actual expressions only; not a permissive YAML/Actions evaluator.
    if expression==NEW_REF:return 'main' if ref=='refs/heads/main' else sha
    if expression==OLD_REF:
        return 'main' if event=='push' and message.startswith('data: clock recovery request daily ') else sha
    raise AssertionError('unreviewed checkout expression')

def retained_fixture():
    if os.environ.get('GITHUB_ACTIONS')=='true':
        base=Path(tempfile.gettempdir())/'doge-daily-serial-checkout-protected-fixtures'
    else:base=HERE.parent/'retained-git-fixtures'
    root=base/uuid.uuid4().hex[:12];root.mkdir(parents=True)
    return root

class DailySerialCheckout(unittest.TestCase):
    def test_all_main_events_checkout_lock_time_main(self):
        sha='a'*40
        cases=[('push','fix: ordinary maintenance'),('push','data: clock recovery request daily 2026-11-10'),
            ('schedule',''),('workflow_dispatch','')]
        for event,message in cases:
            with self.subTest(event=event,message=message):
                self.assertEqual(ref_for(workflow_ref(),event,'refs/heads/main',sha,message),'main')

    def test_nonmain_manual_branch_retains_exact_event_sha(self):
        for branch in ('refs/heads/research/example','refs/heads/main-lookalike','refs/tags/example'):
            with self.subTest(branch=branch):
                self.assertEqual(ref_for(workflow_ref(),'workflow_dispatch',branch,'b'*40),'b'*40)

    def test_only_exact_reviewed_main_ref_expression_is_admitted(self):
        self.assertEqual(workflow_ref(),NEW_REF)

    def test_existing_serial_lock_budget_and_admission_precede_all_writers(self):
        text=WORKFLOW.read_text(encoding='utf-8')
        self.assertIn('  group: doge-history-collector\n  cancel-in-progress: false',text)
        self.assertIn('    timeout-minutes: 20',text);self.assertIn('          fetch-depth: 0',text)
        self.assertLess(text.index('id: clock_request'),text.index('run: python doge_history.py'))
        self.assertIn('run: python -B clock_recovery_request.py --kind daily',text)
        self.assertIn('python -B runtime_history_graph.py\n          python report_v41.py',text)
        self.assertIn('DOGE_REPORT_PLUS_ONLINE: \'1\'',text)
        self.assertIn('for attempt in 1 2 3 4 5; do',text)
        self.assertIn('git pull --rebase origin main && git push origin HEAD:main',text)
        self.assertNotIn('git push --force',text)
        self.assertNotIn('git checkout --ours',text);self.assertNotIn('git checkout --theirs',text)

    def test_real_serial_writers_preserve_preceding_snapshot_and_append_rows(self):
        root=retained_fixture();origin=root/'origin.git';a=root/'previous-writer';stale=root/'stale-control';actual=root/'queued-writer'
        env={k:v for k,v in os.environ.items() if not k.upper().startswith('GIT_')}
        env.update(GIT_TERMINAL_PROMPT='0',GIT_CONFIG_NOSYSTEM='1',GIT_CONFIG_GLOBAL=os.devnull,
            GIT_OPTIONAL_LOCKS='0',GIT_AUTHOR_DATE='2026-11-10T07:50:56Z',GIT_COMMITTER_DATE='2026-11-10T07:50:56Z')
        commands=[]
        def git(cwd,*args,check=True):
            result=subprocess.run([GIT,*map(str,args)],cwd=cwd,env=env,capture_output=True,text=True,timeout=20)
            commands.append({'cwd':str(cwd),'args':list(map(str,args)),'exit':result.returncode,
                'stdout':result.stdout,'stderr':result.stderr})
            if check and result.returncode:raise RuntimeError('local Git fixture '+str(args)+': '+result.stderr)
            return result
        def configure(repo):
            git(repo,'config','user.name','serial daily fixture')
            git(repo,'config','user.email','serial-daily@example.invalid')
            git(repo,'config','core.autocrlf','false');git(repo,'config','core.longpaths','true')
        def write(repo,relative,value):
            path=repo/relative;path.parent.mkdir(parents=True,exist_ok=True)
            path.write_text(json.dumps(value,sort_keys=True,indent=2)+'\n',encoding='utf-8')
        def commit(repo,message):
            git(repo,'add','--all');git(repo,'commit','-qm',message)
            return git(repo,'rev-parse','HEAD').stdout.strip()
        git(root,'init','--bare','--initial-branch=main',origin)
        git(root,'--git-dir',origin,'config','core.longpaths','true')
        git(root,'clone',origin,a);configure(a)
        for rel in SNAPSHOTS:write(a,rel,{'generation':'S','retained_seed':'synthetic seed'})
        reg='data/forecast_v41/registry.csv'
        (a/reg).write_text('forecast_id,value\nseed,1\n',encoding='utf-8')
        event_sha=commit(a,'synthetic original event S');git(a,'push','-u','origin','main')
        # This invocation was enqueued while main still was S.
        git(root,'clone',origin,stale);configure(stale)
        for rel in SNAPSHOTS:write(a,rel,{'generation':'A','retained_seed':'synthetic seed','previous_writer_receipt':'A'})
        with (a/reg).open('a',encoding='utf-8') as handle:handle.write('prior_writer_A,2\n')
        preceding_sha=commit(a,'data: synthetic preceding serialized daily writer A')
        git(a,'push','origin','main')
        # A releases the same serial lock before B starts checkout.
        git(stale,'checkout','--detach',event_sha)
        for rel in SNAPSHOTS:write(stale,rel,{'generation':'B','retained_seed':'synthetic seed'})
        commit(stale,'synthetic stale B generated from S')
        stale_result=git(stale,'pull','--rebase','origin','main',check=False)
        self.assertNotEqual(stale_result.returncode,0,'control must reproduce real overlapping JSON conflict')
        self.assertIn('CONFLICT',stale_result.stdout+stale_result.stderr)
        # Counterfactual B uses the actual workflow expression at lock acquisition.
        git(root,'clone',origin,actual);configure(actual)
        selected=ref_for(workflow_ref(),'push','refs/heads/main',event_sha,'fix: ordinary daily workflow maintenance')
        git(actual,'checkout','--detach',selected)
        checked_out=git(actual,'rev-parse','HEAD').stdout.strip()
        for rel in SNAPSHOTS:
            value=json.loads((actual/rel).read_text(encoding='utf-8'));value['generation']='B';write(actual,rel,value)
        commit(actual,'data: synthetic queued daily writer B')
        actual_result=git(actual,'pull','--rebase','origin','main',check=False)
        receipt={'fixture':str(root),'synthetic_only':True,'event_sha_S':event_sha,'preceding_sha_A':preceding_sha,
            'selected_ref':selected,'actual_checkout':checked_out,'stale_rebase_exit':stale_result.returncode,
            'candidate_rebase_exit':actual_result.returncode,'commands':commands,
            'Git_deleted_or_reset':False,'business_model_collector_HTTP_calls':0}
        (root/'serial-writer-receipt.json').write_text(json.dumps(receipt,indent=2)+'\n',encoding='utf-8')
        self.assertEqual(actual_result.returncode,0,actual_result.stdout+actual_result.stderr)
        self.assertEqual(checked_out,preceding_sha)
        for rel in SNAPSHOTS:
            self.assertEqual(json.loads((actual/rel).read_text()),
                {'generation':'B','retained_seed':'synthetic seed','previous_writer_receipt':'A'})
        self.assertEqual((actual/reg).read_text(),'forecast_id,value\nseed,1\nprior_writer_A,2\n')
        git(actual,'push','origin','HEAD:main')
        after=git(root,'--git-dir',origin,'show','main:'+reg).stdout
        self.assertEqual(after,'forecast_id,value\nseed,1\nprior_writer_A,2\n')
        receipt.update(previous_writer_snapshot_and_registry_preserved=True,normal_push_exit=0)
        (root/'serial-writer-receipt.json').write_text(json.dumps(receipt,indent=2)+'\n',encoding='utf-8')

if __name__=='__main__':unittest.main(verbosity=2)
