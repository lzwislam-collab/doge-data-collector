"""Local Git identity fixtures. No network or production replay."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import unittest
import uuid
from unittest.mock import patch

import p0_operational_audit as audit
import p2_paired_execution_v3_1 as engine
import test_p0_operations as old
import p0_source_binding as binding

HERE = Path(__file__).resolve().parent
REFERENCE = Path(engine.__file__).resolve().parent


class LockedPublicationTests(unittest.TestCase):
    IDS = ('ARBITRARY_SOURCE_A', 'ARBITRARY_SOURCE_B')
    REPO = old.CapturedSourceRecoveryTests.REPO
    git = old.CapturedSourceRecoveryTests.git
    commit = old.CapturedSourceRecoveryTests.commit
    write = old.CapturedSourceRecoveryTests.write
    seed = old.CapturedSourceRecoveryTests.seed
    save_proof = old.CapturedSourceRecoveryTests.save_proof

    def setUp(self):
        self.root = HERE.parent/'p0-source-binding-git-fixtures'/uuid.uuid4().hex
        self.root.mkdir(parents=True)
        self.seed()
        for path in engine.CODE_FILES + (engine.POLICY, engine.SYSTEM,
                'system/current_p2_execution_shadow_v3_1.json', engine.source.POLICY,
                engine.source.SYSTEM, 'system/current_paired_shadow.json'):
            target = self.root/path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((REFERENCE/path).read_bytes().replace(b'\r\n', b'\n'))
        fid = self.IDS[0]
        pair = {'production': self.rows[fid], 'decision': {'paper_action': 'NO_TRADE'},
                'previous_sha256': None}
        pair['record_sha256'] = engine.source.digest(pair)
        self.pair = pair
        self.write(engine.source.REG, engine.source.causal.canon(pair)+'\n')
        self.write(engine.source.record_path(self.root/engine.source.REG, fid).relative_to(self.root),
                   engine.source.causal.canon(pair)+'\n')
        self.public_head = self.commit()
        if getattr(self, 'nonancestor_public_head', False):
            branch = self.git('symbolic-ref', '--short', 'HEAD')
            self.git('checkout', '-qb', 'side-witness')
            self.write('side-branch-evidence.txt', 'synthetic side branch\n')
            self.public_head = self.commit()
            self.git('checkout', '-q', branch)
        for fid, witness in self.originals.items():
            witness['head_sha'] = self.public_head
            self.proofs[fid]['publication'] = copy.deepcopy(witness)
            self.server_runs[witness['run_id']]['head_sha'] = self.public_head
        fid = self.IDS[0]
        if getattr(self, 'outside_original_window', False):
            stamp = '2026-10-26T05:46:00+00:00'
            self.originals[fid]['observed_public_utc'] = stamp
            self.proofs[fid]['publication'] = copy.deepcopy(self.originals[fid])
            self.server_runs[self.originals[fid]['run_id']]['created_at'] = stamp
        self.item = {'forecast_id': fid, 'horizon_hours': 1,
            'issued_at_utc': self.rows[fid]['issued_at_utc'],
            'target_utc': self.rows[fid]['target_utc'], 'paper_action': 'NO_TRADE',
            'observed_public_utc': self.originals[fid]['observed_public_utc'],
            'source_verified': True, 'pair_sha256': pair['record_sha256'],
            'source_witness_head_sha': self.public_head, 'source_error': None,
            'paired_audit_verified': True}
        self.event = {'schema_version': 1, 'book_sha256': 'b'*64,
            'snapshot_path': engine.BASE+'/snapshots/bb/'+'b'*64+'.json',
            'quote_utc': '2026-10-26T05:02:00+00:00',
            'captured_at_utc': '2026-10-26T05:02:01+00:00',
            'source_items': [self.item], 'previous_sha256': None, 'real_orders_authorized': False}
        self.event['record_sha256'] = engine.digest(self.event)
        self.write(engine.EVENTS, engine.source.causal.canon(self.event)+'\n')
        if getattr(self, 'without_original_proof', False): self.proofs.pop(fid)
        self.save_proof()  # exact first-book commit and original P0 proof
        self.first_book_commit = self.proof_commit
        self.later = dict(self.originals[fid], run_id='777777',
            observed_public_utc='2026-10-26T05:03:00+00:00',
            run_url=f'https://github.com/{self.REPO}/actions/runs/777777')
        self.server_runs['777777'] = dict(self.server_runs[self.originals[fid]['run_id']],
            id=777777, created_at=self.later['observed_public_utc'], html_url=self.later['run_url'])
        prior_rows = audit.base.read_rows(self.root/audit.base.WITNESSES, audit.base.WCOLS)
        prior_rows[fid] = self.later
        self.write(audit.base.WITNESSES, audit.frozen.serial(prior_rows, audit.base.WCOLS))
        self.legacy['verified_remote_head_witnesses'][fid] = self.later
        self.commit()

    def evaluate(self):
        # The unchanged unrelated scoring/protocol fixtures are supplied by the old suite.
        # P2 guard/history, witness server/ancestor and quote/public checks remain real.
        with patch.object(audit, '_CAPTURED_SOURCE_WITNESSES', {}), \
                patch.object(audit.base, 'github_json',
                    side_effect=lambda path: copy.deepcopy(self.server_runs[path.rsplit('/', 1)[-1]])):
            return old.CapturedSourceRecoveryTests.evaluate(self)

    def test_valid_later_witness_cannot_replace_already_locked_context(self):
        fid = self.IDS[0]
        before = {p: (self.root/p).read_bytes() for p in (engine.EVENTS, audit.base.WITNESSES)}
        result = self.evaluate()
        self.assertIn(fid, result['forecast_proofs'], result['rejected_evidence'])
        self.assertEqual(result['forecast_proofs'][fid]['publication'], self.originals[fid])
        self.assertEqual(before, {p: (self.root/p).read_bytes() for p in before})

    def test_first_proof_cannot_be_replaced_by_mutable_latest(self):
        fid = self.IDS[0]
        self.proofs[fid]['publication'] = copy.deepcopy(self.later)
        self.write('data/p0_operations/latest.json', json.dumps({'forecast_proofs': self.proofs}))
        result = self.evaluate()
        self.assertIn(fid, result['forecast_proofs'], result['rejected_evidence'])
        self.assertEqual(result['forecast_proofs'][fid]['publication'], self.originals[fid])

    def recover(self, item=None):
        with patch.object(audit.base, 'github_json',
                side_effect=lambda path: copy.deepcopy(self.server_runs[path.rsplit('/', 1)[-1]])):
            return binding.recover_locked_publication(self.root, self.rows[self.IDS[0]],
                                                      self.bindings, item or self.item)

    def test_server_identity_changes_fail_closed(self):
        rid = self.originals[self.IDS[0]]['run_id']
        original = copy.deepcopy(self.server_runs[rid])
        for key, value in [('id', 9), ('head_sha', 'f'*40), ('created_at', '2026-10-26T05:01:44Z'),
                ('html_url', 'https://example.test/wrong'), ('repository', {'full_name': 'wrong/repo'}),
                ('head_repository', {'full_name': 'wrong/repo'})]:
            with self.subTest(key=key):
                self.server_runs[rid] = dict(original, **{key: value})
                with self.assertRaisesRegex(ValueError, 'server identity'):
                    self.recover()
        self.server_runs[rid] = original

    def test_nonpublication_source_changes_cannot_be_hidden(self):
        for key, value in [('pair_sha256', 'f'*64), ('paper_action', 'PAPER_LONG'),
                ('issued_at_utc', '2026-10-26T05:00:54+00:00'),
                ('target_utc', '2026-10-26T06:00:54+00:00'), ('horizon_hours', 24),
                ('paired_audit_verified', False)]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.recover(dict(self.item, **{key: value}))

    def test_changed_current_forecast_or_quote_is_still_blocked(self):
        row = self.rows[self.IDS[0]]
        old_value = row['majority_direction']
        row['majority_direction'] = 'DOWN'
        with self.assertRaises(ValueError): self.recover()
        row['majority_direction'] = old_value
        quote = audit.evidence.snapshot_path(self.root, self.bindings[self.IDS[0]+'|ISSUE']['quote_id'])
        quote.write_bytes(b'changed')
        with self.assertRaises(ValueError): self.recover()

    def test_contradictory_context_append_is_rejected_by_projection(self):
        event = dict(self.event, source_items=[dict(self.item, observed_public_utc=self.later['observed_public_utc'])],
            quote_utc='2026-10-26T05:04:00+00:00', captured_at_utc='2026-10-26T05:04:01+00:00',
            previous_sha256=self.event['record_sha256'])
        event.pop('record_sha256'); event['record_sha256'] = engine.digest(event)
        with (self.root/engine.EVENTS).open('a', encoding='utf-8', newline='\n') as stream:
            stream.write(engine.source.causal.canon(event)+'\n')
        with self.assertRaisesRegex(ValueError, 'contradictory'):
            binding.locked_contexts(self.root)

    def test_missing_first_book_proof_cannot_be_inferred_from_later_csv(self):
        self.without_original_proof = True
        self.setUp()
        with self.assertRaisesRegex(ValueError, 'original P0 proof mismatch'):
            self.recover()

    def test_nonancestor_public_head_remains_blocked(self):
        self.nonancestor_public_head = True
        self.setUp()
        with self.assertRaises((ValueError, subprocess.CalledProcessError)):
            self.recover()

    def test_recorded_context_cannot_override_original_publication_window(self):
        self.outside_original_window = True
        self.setUp()
        with self.assertRaisesRegex(ValueError, 'original publication window'):
            self.recover()


class SnapshotDependencyTests(unittest.TestCase):
    def setUp(self):
        self.root = HERE.parent/'p0-source-binding-git-fixtures'/uuid.uuid4().hex
        self.root.mkdir(parents=True)
        import runtime_audit_worker as worker
        self.worker = worker
        for relative in worker.INPUTS:
            path = self.root/relative; path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('source', encoding='utf-8')
        path = self.root/audit.base.WITNESSES; path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(','.join(audit.base.WCOLS)+'\n', encoding='utf-8')

    def append_event(self, fid, previous=None, minute=1):
        item = {'forecast_id': fid, 'source_verified': True, 'observed_public_utc': '2026-10-26T05:00:00Z'}
        event = {'source_items': [item], 'quote_utc': f'2026-10-26T05:{minute:02d}:00Z',
                 'previous_sha256': previous}
        event['record_sha256'] = engine.digest(event)
        path = self.root/engine.EVENTS; path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('a', encoding='utf-8', newline='\n') as stream:
            stream.write(engine.source.causal.canon(event)+'\n')
        return event['record_sha256']

    def test_csv_change_invalidates_old_worker_inputs(self):
        before = self.worker.fingerprint(self.root)
        with (self.root/audit.base.WITNESSES).open('a', encoding='utf-8') as stream:
            stream.write('new-binding\n')
        self.assertNotEqual(before, self.worker.fingerprint(self.root))

    def test_absent_csv_never_collides_with_literal_marker_file(self):
        root = self.root/'missing-csv'
        for relative in self.worker.INPUTS:
            path = root/relative; path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((self.root/relative).read_bytes())
        before = self.worker.fingerprint(root)
        path = root/audit.base.WITNESSES; path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'absent')
        self.assertNotEqual(before, self.worker.fingerprint(root))

    def test_first_new_context_invalidates_but_repeat_book_does_not(self):
        before = self.worker.fingerprint(self.root)
        first = self.append_event('NEW_ARBITRARY_SOURCE')
        locked = self.worker.fingerprint(self.root)
        self.assertNotEqual(before, locked)
        self.append_event('NEW_ARBITRARY_SOURCE', first, 2)
        self.assertEqual(locked, self.worker.fingerprint(self.root))

    def test_old_snapshot_cannot_install_reports_after_csv_append(self):
        result = {'status': 'ok', 'input_head': 'fixture', 'inputs': self.worker.fingerprint(self.root),
            'outputs': {path: '{"status":"ok"}' for path in self.worker.OUTPUTS}, 'timings': []}
        with (self.root/audit.base.WITNESSES).open('a', encoding='utf-8') as stream:
            stream.write('new-binding\n')
        self.assertEqual(self.worker.accept(self.root, result)['status'], 'discarded_input_changed')
        self.assertFalse(any((self.root/path).exists() for path in self.worker.OUTPUTS))

    def test_unreviewed_helper_bytes_are_rejected_before_import(self):
        self.assertTrue(callable(getattr(audit, '_source_binding_backend', None)),
                        'source helper authentication is missing')
        (self.root/'p0_source_binding.py').write_text('raise RuntimeError("must not execute")', encoding='utf-8')
        with patch.dict(audit._source_binding_backend.__globals__, {'__file__': str(self.root/'p0_operational_audit.py')}):
            with self.assertRaisesRegex(ValueError, 'source binding implementation'):
                audit._source_binding_backend()


if __name__ == '__main__':
    unittest.main()
