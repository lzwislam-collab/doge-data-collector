"""First binding tests use original validators and a local bare Git remote."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import unittest
import uuid
from unittest.mock import patch
from datetime import datetime, timezone

import p0_source_binding as binding
import runtime_audit_worker as worker
import p0_operational_audit as audit
import publish_forward_evidence as publisher
import p2_paired_execution_v3_1 as engine
import test_p0_source_binding as fixture_tests


class FirstBindingTests(unittest.TestCase):
    IDS = fixture_tests.LockedPublicationTests.IDS
    REPO = fixture_tests.LockedPublicationTests.REPO
    git = fixture_tests.LockedPublicationTests.git
    commit = fixture_tests.LockedPublicationTests.commit
    write = fixture_tests.LockedPublicationTests.write
    seed = fixture_tests.LockedPublicationTests.seed
    save_proof = fixture_tests.LockedPublicationTests.save_proof

    def setUp(self):
        fixture_tests.LockedPublicationTests.setUp(self)
        self.now = datetime(2026, 10, 26, 13, 3, tzinfo=timezone.utc)
        self.fid = 'UNSCOPED'
        row = self.rows[self.fid]
        pair = {'production': row, 'decision': {'paper_action': 'NO_TRADE'},
                'previous_sha256': self.pair['record_sha256']}
        pair['record_sha256'] = engine.source.digest(pair)
        with (self.root/engine.source.REG).open('a', encoding='utf-8', newline='\n') as stream:
            stream.write(engine.source.causal.canon(pair)+'\n')
        self.write(engine.source.record_path(self.root/engine.source.REG, self.fid).relative_to(self.root),
                   engine.source.causal.canon(pair)+'\n')
        head = self.commit()
        self.witness = {'forecast_id': self.fid, 'run_id': '888888', 'head_sha': head,
            'observed_public_utc': '2026-10-26T13:02:10+00:00',
            'run_url': f'https://github.com/{self.REPO}/actions/runs/888888'}
        self.server_runs['888888'] = {'id': 888888, 'head_sha': head,
            'created_at': '2026-10-26T13:02:10Z', 'html_url': self.witness['run_url'],
            'repository': {'full_name': self.REPO}, 'head_repository': {'full_name': self.REPO}}
        self.operations = {'status': 'ok', 'audited_at_utc': self.now.isoformat(),
            'forecast_proofs': {self.fid: {'issued_at_utc': row['issued_at_utc'],
                'source_quote_utc': row['source_quote_utc'],
                'quote_id': self.bindings[self.fid+'|ISSUE']['quote_id'], 'publication': self.witness}}}
        paired = {'status': 'ok', 'evaluated_at_utc': self.now.isoformat(),
            'input_registry_sha256': hashlib.sha256((self.root/audit.base.REG).read_bytes()).hexdigest(),
            'protocol_id': json.loads((self.root/engine.source.POLICY).read_bytes())['protocol_id'],
            'registered_pairs': 2, 'rejected_or_missing': {}}
        self.write(audit.base.SCORES, audit.frozen.serial({}, audit.frozen.SCOLS))
        result = {'status': 'ok', 'input_head': self.git('rev-parse', 'HEAD'),
            'inputs': worker.fingerprint(self.root), 'timings': [],
            'outputs': {worker.OUTPUTS[0]: json.dumps(self.operations)+'\n',
                        worker.OUTPUTS[1]: json.dumps(paired)+'\n'}}
        self.ready = worker.accept(self.root, result)

    def prepare(self, basis=None):
        self.assertTrue(callable(getattr(binding, 'prepare_first_book_bindings', None)),
                        'pre-book durable binding gate is missing')
        with patch.object(audit.base, 'github_json',
                side_effect=lambda path: copy.deepcopy(self.server_runs[path.rsplit('/', 1)[-1]])):
            return binding.prepare_first_book_bindings(self.root, self.operations, self.now,
                                                       basis=self.ready.get('basis') if basis is None else basis)

    def test_unknown_basis_cannot_prepare_or_capture(self):
        before = (self.root/audit.base.WITNESSES).read_bytes()
        with self.assertRaisesRegex(ValueError, 'basis'):
            self.prepare({})
        self.assertEqual(before, (self.root/audit.base.WITNESSES).read_bytes())

    def test_changed_scientific_input_after_prepare_cannot_use_equivalence(self):
        plan = self.prepare()
        path = self.root/'data/hourly_research/ohlc.csv'
        path.write_bytes(path.read_bytes()+b'changed\n')
        with self.assertRaisesRegex(ValueError, 'transition changed'):
            binding.verify_first_book_bindings(self.root, plan, {'status': 'verified_remote_readback'})

    def test_new_report_with_same_publication_is_not_accepted_basis(self):
        plan = self.prepare()
        changed = dict(self.operations, audited_at_utc='2026-10-26T13:03:01+00:00')
        self.write(worker.OUTPUTS[0], json.dumps(changed))
        with self.assertRaisesRegex(ValueError, 'transition changed'):
            binding.verify_first_book_bindings(self.root, plan, {'status': 'verified_remote_readback'})

    def test_real_old_remote_tree_is_not_a_binding_ack(self):
        plan = self.prepare()
        head = self.git('rev-parse', 'HEAD')
        self.git('fetch', '--no-tags', '.', 'HEAD')
        with self.assertRaisesRegex(ValueError, 'exact remote ACK'):
            binding.verify_first_book_bindings(self.root, plan,
                {'status': 'verified_remote_readback', 'head': head,
                 'verified_paths': [audit.base.WITNESSES]})

    def test_changed_row_after_prepare_is_not_a_known_transition(self):
        plan = self.prepare()
        path = self.root/audit.base.WITNESSES
        path.write_bytes(path.read_bytes().replace(b'888888', b'999999'))
        with self.assertRaisesRegex(ValueError, 'transition changed'):
            binding.verify_prepared_transition(self.root, plan)

    def test_existing_different_csv_witness_is_never_overwritten(self):
        rows = audit.base.read_rows(self.root/audit.base.WITNESSES, audit.base.WCOLS)
        rows[self.fid] = dict(self.witness, run_id='999999',
            run_url=f'https://github.com/{self.REPO}/actions/runs/999999')
        self.write(audit.base.WITNESSES, audit.frozen.serial(rows, audit.base.WCOLS))
        before = (self.root/audit.base.WITNESSES).read_bytes()
        with self.assertRaises(ValueError): self.prepare()
        self.assertEqual(before, (self.root/audit.base.WITNESSES).read_bytes())

    def test_original_writer_and_remote_readback_finish_before_capture_permission(self):
        before = (self.root/audit.base.WITNESSES).read_bytes()
        self.git('branch', '-M', 'main')
        remote = Path(__file__).resolve().parent.parent/'p0-binding-test-git-v1'/uuid.uuid4().hex[:8]
        remote.parent.mkdir(exist_ok=True)
        subprocess.check_call(['git', 'init', '--bare', '-q', str(remote)])
        subprocess.check_call(['git', '--git-dir', str(remote), 'config', 'core.longpaths', 'true'])
        self.git('remote', 'add', 'origin', str(remote))
        self.git('push', '-q', 'origin', 'HEAD:main')
        plan = self.prepare()
        self.assertTrue(plan['requires_publication'])
        after = (self.root/audit.base.WITNESSES).read_bytes()
        for line in before.splitlines(keepends=True): self.assertIn(line, after)
        with self.assertRaises(ValueError):
            binding.verify_first_book_bindings(self.root, plan, {'status': 'push_result_unknown'})
        for path in (audit.base.WITNESSES, *worker.OUTPUTS): self.git('add', '--', path)
        self.git('config', 'user.name', 'Offline QA')
        self.git('config', 'user.email', 'qa@example.test')
        result = publisher.publish(self.root)
        self.assertTrue(publisher.verify_readback(self.root, 'FETCH_HEAD', {audit.base.WITNESSES: after}))
        ack = {'status': 'verified_remote_readback', 'head': result['remote_head'],
               'verified_paths': [audit.base.WITNESSES]}
        binding.verify_first_book_bindings(self.root, plan, ack)
        self.assertEqual(after, publisher.command(self.root, 'show', 'FETCH_HEAD:'+audit.base.WITNESSES))

    def adapter(self, *, fail_publish=False):
        import runtime_deadline_service as runtime
        trace = []
        class Adapter(runtime.LiveAdapter):
            prospective_enabled = False
            def action(owned, name):
                if name == 'publish':
                    trace.append('publish')
                    if fail_publish: raise RuntimeError('unknown push result')
                    return {'status': 'verified_remote_readback'}
                return super().action(name)
            def source_ready(owned): return True
            def python(owned, script, mode): trace.append(mode); return '{}'
        adapter = object.__new__(Adapter)
        adapter.root = self.root
        adapter.accepted_source_basis = self.ready['basis']
        adapter.pending_source_binding = None
        adapter.last_qualified_book = {}
        return adapter, trace

    def test_concrete_adapter_requires_publish_and_verified_ack_before_capture(self):
        import runtime_deadline_service as runtime
        adapter, trace = self.adapter()
        def verify(root, plan, publication): trace.append('verify'); return plan['after_basis']
        with patch.object(audit.base, 'github_json',
                side_effect=lambda path: copy.deepcopy(self.server_runs[path.rsplit('/', 1)[-1]])), \
                patch.object(binding, 'verify_first_book_bindings', side_effect=verify), \
                patch.object(engine, 'event_books', return_value=iter([{}])), \
                patch.object(runtime, 'qualified_capture', return_value=False), \
                patch.object(runtime, 'datetime') as clock:
            clock.now.return_value = self.now
            adapter.action('book')
        self.assertEqual(trace, ['publish', 'verify', 'capture'])

    def test_concrete_adapter_unknown_push_never_reaches_capture(self):
        import runtime_deadline_service as runtime
        adapter, trace = self.adapter(fail_publish=True)
        with patch.object(audit.base, 'github_json',
                side_effect=lambda path: copy.deepcopy(self.server_runs[path.rsplit('/', 1)[-1]])), \
                patch.object(engine, 'event_books', return_value=iter([{}])), \
                patch.object(runtime, 'qualified_capture', return_value=False), \
                patch.object(runtime, 'datetime') as clock:
            clock.now.return_value = self.now
            with self.assertRaisesRegex(RuntimeError, 'unknown push'):
                adapter.action('book')
        self.assertEqual(trace, ['publish'])

    def test_already_committed_binding_gets_explicit_ack_before_first_book(self):
        import runtime_deadline_service as runtime
        self.test_original_writer_and_remote_readback_finish_before_capture_permission()
        adapter = object.__new__(runtime.LiveAdapter)
        adapter.root = self.root
        adapter.prospective_enabled = False
        adapter.core_witness_pending = False
        adapter.last_verified_publication = None
        adapter.pending_source_binding = self.prepare(basis=binding.current_basis(self.root))
        # This synthetic historical book intentionally has no raw book snapshot;
        # its independent raw-book and unrelated V2 protocol guards are outside this ACK test.
        with patch.object(runtime, 'validate_p2'), patch.object(runtime, 'validate_publication_state'):
            ack = adapter.action('publish')
        self.assertIn(audit.base.WITNESSES, ack['verified_paths'])
        binding.verify_first_book_bindings(self.root, adapter.pending_source_binding, ack)


if __name__ == '__main__': unittest.main()
