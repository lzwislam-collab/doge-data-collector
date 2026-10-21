import copy
import base64
import hashlib
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from pathlib import Path

import e1_observer as observer


START = datetime(2026, 10, 21, tzinfo=timezone.utc)


class FakeProofs:
    def __init__(self, missing=(), error=None):
        self.missing = set(missing)
        self.error = error
        self.calls = []

    def find(self, required, before, after=None, pinned=(), discover=True,
             discover_from=None):
        self.calls.append((required, before, after, tuple(pinned), discover))
        if self.error:
            raise self.error
        return None if any(part in next(iter(required)) for part in self.missing) else {'run_id': 7, 'head_sha': 'b' * 40}


def state(events=None, scores=None):
    return observer.Snapshot(
        policy={'protocol_id': 'E1', 'effective_from_utc': START.isoformat(),
                'observation_end_utc': (START + timedelta(days=90)).isoformat(),
                'slot_seconds': 7200, 'issue_window_seconds': 2700,
                'horizon_seconds': 3600, 'target_window_seconds': 1500},
        registration={'registration_identity': 'registered'}, files={},
        events=list(events or []), scores=list(scores or []), attempts=[],
        bindings={}, source_commit='a' * 40)


def event(offset=0):
    slot = START + timedelta(seconds=offset)
    issued = slot + timedelta(minutes=12)
    return {'event_id': str(int(slot.timestamp())) + '_formal', 'cohort': 'formal',
            'protocol_id': 'E1', 'slot_utc': slot.isoformat(),
            'issued_at_utc': issued.isoformat(),
            'target_utc': (issued + timedelta(hours=1)).isoformat(),
            'source_run_id': 5, 'input_source_commit': 'b' * 40}


class DetectorTests(unittest.TestCase):
    def audit(self, snapshot, at, proofs=None, **kwargs):
        return observer.audit(snapshot, at, proofs or FakeProofs(),
                              validate_event=lambda e: b'source',
                              validate_score=kwargs.get('validate_score', lambda s, e: None))

    def kinds(self, report):
        return [a['kind'] for a in report['anomalies']]

    def test_issuance_deadline_and_stable_key(self):
        self.assertEqual(self.kinds(self.audit(state(), START + timedelta(seconds=2699))), [])
        due = self.audit(state(), START + timedelta(seconds=2700))
        later = self.audit(state(), START + timedelta(seconds=2800))
        self.assertEqual(self.kinds(due), ['MISSING_ISSUANCE'])
        self.assertEqual(due['anomalies'][0]['key'], later['anomalies'][0]['key'])
        self.assertEqual(len(due['anomalies'][0]['key']), 64)

    def test_actual_target_not_nominal_slot_controls_stage_deadlines(self):
        e = event()
        proofs = FakeProofs(missing=('events/', 'scores/'))
        at = START + timedelta(minutes=71, seconds=59)
        self.assertEqual(self.kinds(self.audit(state([e]), at, proofs)), [])
        pred = self.audit(state([e]), at + timedelta(seconds=1), proofs)
        self.assertEqual(self.kinds(pred), ['MISSING_PREDICTION_WITNESS'])
        scoredue = self.audit(state([e]), START + timedelta(minutes=97), proofs)
        self.assertEqual(set(self.kinds(scoredue)), {'MISSING_PREDICTION_WITNESS', 'MISSING_SCORE'})
        self.assertNotIn('MISSING_TARGET_WITNESS', self.kinds(scoredue))

    def test_all_events_not_only_latest_four_and_end_has_1080_slots(self):
        es = [event(i * 7200) for i in range(6)]
        ss = [{'event_id': e['event_id']} for e in es]
        report = self.audit(state(es, ss), START + timedelta(hours=13))
        self.assertEqual(report['event_count'], 6)
        self.assertEqual(self.kinds(report).count('MISSING_ISSUANCE'), 1)
        end = self.audit(state(), START + timedelta(days=91))
        self.assertEqual(len(end['anomalies']), 1080)

    def test_valid_score_missing_target_witness_only_after_25_minutes(self):
        e = event(); s = {'event_id': e['event_id']}
        p = FakeProofs(missing=('scores/',))
        self.assertEqual(self.kinds(self.audit(state([e], [s]), START + timedelta(minutes=96, seconds=59), p)), [])
        self.assertEqual(self.kinds(self.audit(state([e], [s]), START + timedelta(minutes=97), p)), ['MISSING_TARGET_WITNESS'])

    def test_invalid_score_is_not_missing_and_cost_error_is_not_missing(self):
        e = event(); s = {'event_id': e['event_id'], 'cost_error': 'depth unavailable'}
        report = self.audit(state([e], [s]), START + timedelta(minutes=98))
        self.assertNotIn('MISSING_SCORE', self.kinds(report))
        invalid = self.audit(state([e], [s]), START + timedelta(minutes=98),
                             validate_score=lambda s, e: (_ for _ in ()).throw(ValueError('late target')))
        self.assertEqual(self.kinds(invalid), ['INVALID_SCORE'])

    def test_future_duplicate_wrong_target_and_orphan_score_are_integrity_failures(self):
        e = event()
        bad = copy.deepcopy(e); bad['target_utc'] = (START + timedelta(hours=4)).isoformat()
        for snapshot, at in [(state([e]), START), (state([e, e]), START + timedelta(minutes=20)),
                             (state([bad]), START + timedelta(minutes=20)),
                             (state([], [{'event_id': e['event_id']}]), START + timedelta(minutes=20))]:
            with self.subTest(snapshot=snapshot):
                self.assertEqual(self.kinds(self.audit(snapshot, at)), ['INTEGRITY_FAILURE'])

    def test_auth_failure_discards_partial_missing_findings(self):
        e = event()
        report = self.audit(state([e]), START + timedelta(hours=3), FakeProofs(error=PermissionError('secret token')))
        self.assertEqual(report['status'], 'OBSERVATION_FAILED')
        self.assertEqual(self.kinds(report), ['OBSERVATION_FAILED'])
        self.assertNotIn('secret token', str(report))

    def test_source_only_uses_event_run_and_source_is_checked_even_before_target(self):
        e = event(); p = FakeProofs(missing=('ohlc.csv',))
        report = self.audit(state([e]), START + timedelta(minutes=20), p)
        self.assertEqual(self.kinds(report), ['MISSING_SOURCE_WITNESS'])
        self.assertEqual(p.calls[0][3:], ((5,), False))

    def test_event_hash_failure_is_integrity_not_a_missing_forecast(self):
        snapshot = state([event()])
        value = observer.audit(snapshot, START + timedelta(hours=3), FakeProofs(),
                               lambda e: (_ for _ in ()).throw(ValueError('source mismatch')),
                               lambda s, e: None)
        self.assertEqual(self.kinds(value), ['INTEGRITY_FAILURE'])

    def test_loader_transport_failure_still_returns_deliverable_report(self):
        with patch.object(observer.frozen, 'git', return_value=b'abc\n'):
            value = observer.observe(now=START,
                loader=lambda: (_ for _ in ()).throw(TimeoutError('signed-secret-url')))
        self.assertEqual(value['schema'], 'DOGE_E1_OBSERVER_V1')
        self.assertEqual(value['status'], 'OBSERVATION_FAILED')
        self.assertEqual(self.kinds(value), ['OBSERVATION_FAILED'])
        self.assertNotIn('signed-secret-url', str(value))

    def test_cutoff_prevents_false_gap_when_checkout_straddles_deadline(self):
        actual = START + timedelta(minutes=46)
        cutoff = START + timedelta(minutes=44)
        value = observer.audit(state(), actual, FakeProofs(), lambda e: b'source',
                               lambda s, e: None, cutoff=cutoff)
        self.assertEqual(self.kinds(value), [])
        self.assertEqual(value['checked_at_utc'], actual.isoformat())
        self.assertEqual(value['evidence_cutoff_utc'], cutoff.isoformat())

    def test_event_seen_after_cutoff_is_not_future_if_before_actual_clock(self):
        actual = START + timedelta(minutes=20)
        cutoff = START + timedelta(minutes=10)
        value = observer.audit(state([event()]), actual, FakeProofs(), lambda e: b'source',
                               lambda s, e: None, cutoff=cutoff)
        self.assertEqual(self.kinds(value), [])

    def test_bad_cutoff_is_an_observation_failure(self):
        for cutoff in [START + timedelta(seconds=1), datetime(2026, 10, 21)]:
            value = observer.audit(state(), START, FakeProofs(), lambda e: b'source',
                                   lambda s, e: None, cutoff=cutoff)
            self.assertEqual(value['status'], 'OBSERVATION_FAILED')
            self.assertEqual(self.kinds(value), ['OBSERVATION_FAILED'])

    def test_source_proof_wrong_snapshot_sha_is_integrity_failure(self):
        p = FakeProofs()
        p.find = lambda *a, **k: {'run_id': 5, 'head_sha': 'c' * 40}
        self.assertEqual(self.kinds(self.audit(state([event()]), START + timedelta(minutes=20), p)),
                         ['INTEGRITY_FAILURE'])

    def test_all_capture_attempts_have_actual_clock_and_original_bytes(self):
        e = event(); at = START + timedelta(minutes=80)
        packet = {'requested_at_utc': at.isoformat(), 'captured_at_utc': at.isoformat(),
                  'source_urls': observer.frozen.parent.old.URLS,
                  'raw_base64': {'ticker': base64.b64encode(b'original').decode()},
                  'raw_text': {'ticker': 'original'},
                  'raw_sha256': {'ticker': hashlib.sha256(b'original').hexdigest()}}
        attempt = {'event_id': e['event_id'] + '_attempt_' + str(int(at.timestamp() * 1000)),
                   'forecast_event_id': e['event_id'], 'packet': packet}
        snapshot = state([e]); snapshot.attempts = [attempt]
        observer.validate_attempts(snapshot, at)
        with self.assertRaises(ValueError):
            observer.validate_attempts(snapshot, at - timedelta(seconds=1))
        packet['raw_text']['ticker'] = 'changed'
        with self.assertRaises(ValueError):
            observer.validate_attempts(snapshot, at)

    def test_failure_cli_preserves_json_but_returns_nonzero(self):
        import json
        value = observer.failed(state(), START, TimeoutError())
        with tempfile.TemporaryDirectory() as tmp, patch.object(observer.frozen, 'ROOT', Path(tmp)), \
                patch.object(observer, 'observe', return_value=value), patch('builtins.print'):
            self.assertEqual(observer.main(), 1)
            saved = json.loads((Path(tmp) / 'e1-observer-output/report.json').read_bytes())
        self.assertEqual(saved['status'], 'OBSERVATION_FAILED')


class WitnessTests(unittest.TestCase):
    def fixture(self, rid, *, finished='2026-10-21T00:30:00Z'):
        import e1_forward as e
        return {'id': rid, 'repository': {'full_name': e.parent.old.REPO},
                'path': e.WITNESS, 'head_branch': 'main', 'head_sha': f'{rid:040x}',
                'status': 'completed', 'conclusion': 'success', 'event': 'workflow_run',
                'created_at': '2026-10-21T00:13:00Z', 'run_attempt': 1}, {
                'name': 'witness', 'status': 'completed', 'conclusion': 'success',
                'started_at': '2026-10-21T00:14:00Z', 'completed_at': finished}

    def store(self, pages, pinned=None, wrong_heads=()):
        import e1_forward as e
        calls = []
        runs = {r['id']: r for rows in pages for r, j in rows}
        jobs = {r['id']: j for rows in pages for r, j in rows}
        if pinned:
            runs[pinned[0]['id']] = pinned[0]; jobs[pinned[0]['id']] = pinned[1]
        def api(path):
            calls.append(path)
            if '/workflows/' in path:
                from urllib.parse import parse_qs, urlsplit
                page = int(parse_qs(urlsplit(path).query)['page'][0])
                rows = pages[page - 1] if page <= len(pages) else []
                return {'workflow_runs': [r for r, j in rows], 'total_count': sum(map(len, pages))}
            rid = int(path.split('/runs/')[1].split('/')[0])
            return {'jobs': [jobs[rid]], 'total_count': 1} if path.endswith('/jobs?per_page=100&page=1') else runs[rid]
        def reader(head, path):
            return b'' if head in wrong_heads else b'expected'
        return observer.WitnessStore(e, {'frozen.py': b'expected'}, api, reader), calls

    def test_pagination_does_not_turn_absence_on_first_page_into_missing(self):
        first = self.fixture(1); second = self.fixture(2)
        store, calls = self.store([[first], [second]], wrong_heads=(first[0]['head_sha'],))
        proof = store.find({'event.json': b'expected'}, START + timedelta(hours=1),
                           START + timedelta(minutes=12), discover_from=START)
        self.assertEqual(proof['run_id'], 2)
        self.assertEqual(sum('/workflows/' in x for x in calls), 2)

    def test_valid_binding_avoids_listing_and_run_job_reads_are_cached(self):
        pinned = self.fixture(9)
        store, calls = self.store([], pinned=pinned)
        for _ in range(2):
            self.assertEqual(store.find({'event.json': b'expected'}, START + timedelta(hours=1),
                                        pinned=(9,))['run_id'], 9)
        self.assertFalse(any('/workflows/' in x for x in calls))
        self.assertEqual(len(calls), 2)

    def test_exact_finish_deadline_is_rejected_and_job_before_issue_is_rejected(self):
        late = self.fixture(1, finished='2026-10-21T01:00:00Z')
        store, _ = self.store([[late]])
        self.assertIsNone(store.find({'event.json': b'expected'}, START + timedelta(hours=1)))
        early = self.fixture(2)
        store, _ = self.store([[early]])
        self.assertIsNone(store.find({'event.json': b'expected'}, START + timedelta(hours=1),
                                     START + timedelta(minutes=15)))

    def test_truncated_or_unstable_pagination_never_means_no_witness(self):
        import e1_forward as e
        for payload in [{'workflow_runs': [], 'total_count': 1001},
                        {'workflow_runs': [], 'total_count': 1}]:
            store = observer.WitnessStore(e, {}, lambda path: payload, lambda h, p: b'')
            with self.assertRaises(RuntimeError):
                store.find({'event.json': b'expected'}, START + timedelta(hours=1))

    def test_invalid_binding_falls_back_to_complete_range(self):
        bad = self.fixture(1); bad[0]['head_branch'] = 'feature'
        good = self.fixture(2)
        store, calls = self.store([[good]], pinned=bad)
        self.assertEqual(store.find({'event.json': b'expected'}, START + timedelta(hours=1),
                                    pinned=(1,))['run_id'], 2)
        self.assertTrue(any('/workflows/' in path for path in calls))

    def test_blob_reads_are_cached_and_transport_errors_propagate(self):
        import e1_forward as e
        reads = []
        def read(head, path):
            reads.append((head, path)); return b'original'
        store = observer.WitnessStore(e, {}, reader=read)
        self.assertEqual(store.read('a' * 40, 'event'), b'original')
        self.assertEqual(store.read('a' * 40, 'event'), b'original')
        self.assertEqual(len(reads), 1)
        store = observer.WitnessStore(e, {}, reader=lambda h, p: (_ for _ in ()).throw(PermissionError()))
        with self.assertRaises(PermissionError):
            store.read('a' * 40, 'event')

    def test_valueerror_read_failure_cannot_be_swallowed_as_missing_source(self):
        pinned = self.fixture(5)
        pinned[0]['created_at'] = '2026-10-21T00:01:00Z'
        pinned[1].update(started_at='2026-10-21T00:02:00Z', completed_at='2026-10-21T00:03:00Z')
        store, _ = self.store([], pinned=pinned)
        store.reader = lambda h, p: (_ for _ in ()).throw(ValueError('unexpected pinned redirect'))
        value = observer.audit(state([event()]), START + timedelta(minutes=20), store,
                               lambda e: b'source', lambda s, e: None)
        self.assertEqual(value['status'], 'OBSERVATION_FAILED')
        self.assertEqual([a['kind'] for a in value['anomalies']], ['OBSERVATION_FAILED'])

    def test_malformed_api_run_is_observation_failure_not_missing_witness(self):
        import e1_forward as e
        store = observer.WitnessStore(e, {}, api=lambda p: {}, reader=lambda h, p: b'')
        value = observer.audit(state([event()]), START + timedelta(minutes=20), store,
                               lambda e: b'source', lambda s, e: None)
        self.assertEqual([a['kind'] for a in value['anomalies']], ['OBSERVATION_FAILED'])

    def test_closed_positive_receipt_avoids_all_api_and_blob_reads_on_next_inspection(self):
        import e1_observer_cache as cache
        import e1_forward as e
        proof = self.fixture(9)
        store, calls = self.store([], pinned=proof)
        fp = {'registration_identity': 'a' * 64, 'sha256_lf': {'code': 'b' * 64}}
        positive = cache.PositiveCache(fp)
        first = observer.WitnessStore(e, {'frozen.py': b'expected'}, store.api,
                                      store.reader, positive_cache=positive,
                                      now=START + timedelta(hours=2))
        actual = first.find({'event': b'expected'}, START + timedelta(hours=1), pinned=(9,))
        restored = cache.PositiveCache.from_payload(positive.payload(), fp, START + timedelta(hours=2))
        second = observer.WitnessStore(e, {'frozen.py': b'expected'},
            api=lambda p: (_ for _ in ()).throw(AssertionError('no API on positive receipt')),
            reader=lambda h, p: (_ for _ in ()).throw(AssertionError('no blob read on positive receipt')),
            positive_cache=restored, now=START + timedelta(hours=2))
        self.assertEqual(second.find({'event': b'expected'}, START + timedelta(hours=1), pinned=(9,)), actual)
        self.assertEqual(restored.hits, 1)

    def test_negative_witness_is_not_stored_as_positive_cache(self):
        import e1_observer_cache as cache
        import e1_forward as e
        store, _ = self.store([])
        positive = cache.PositiveCache({'registration_identity': 'a' * 64, 'sha256_lf': {}})
        actual = observer.WitnessStore(e, {}, store.api, store.reader,
                                       positive_cache=positive, now=START + timedelta(hours=2))
        self.assertIsNone(actual.find({'event': b'expected'}, START + timedelta(hours=1)))
        self.assertEqual(positive.payload()['proofs'], {})


if __name__ == '__main__':
    unittest.main()
