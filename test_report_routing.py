"""Offline behavior tests: historical notices must never freshen old evidence."""
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

try:
    import report_routing as routing
except ModuleNotFoundError as exc:
    if exc.name != 'report_routing':
        raise
    routing = None

NOW = datetime(2026, 11, 11, 12, tzinfo=timezone.utc)
SPOT = 'reports/DOGE_SPOT_PLUS_DAILY.md'
V2 = 'reports/DOGE_FORWARD_VALIDATION_V2_DAILY.md'
SPOT_JSON = 'data/stage5/spot_plus/evolution_latest.json'
V2_JSON = 'data/stage5/forward_validation_v2/daily_latest.json'
BEGIN = b'<!-- DOGE-P1-6-LEGACY-NOTICE:BEGIN -->\n'
END = b'<!-- DOGE-P1-6-LEGACY-NOTICE:END -->\n\n'
SPOT_TIME = '2026-10-13T05:54:44.725456+00:00'
V2_TIME = '2026-10-30T11:37:10.396632+00:00'


class ReportRoutingTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(routing, 'Historical report annotation behavior is missing: report_routing.py is not implemented')
        self.scratch = tempfile.TemporaryDirectory(prefix='routing-fixture-')
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)
        expected = os.environ.get('ROUTING_TEST_TEMP_ROOT')
        if expected:
            self.assertTrue(self.root.resolve().is_relative_to(Path(expected).resolve()), 'Synthetic fixture escaped registered scratch')
        self.bodies = {
            SPOT: ('\ufeff# 旧 Plus\r\n\r\n**运行UTC：** ' + SPOT_TIME + '\r\n保留全部历史正文。\r\n').encode('utf-8'),
            V2: ('# DOGE 正式前瞻验证 V2\n\n截止 UTC：' + V2_TIME + '\n\n原评分不能重写。\n').encode('utf-8'),
        }
        for name, body in self.bodies.items():
            self.write(name, body)
        self.sources = {
            SPOT_JSON: {'status': 'ok', 'schema_version': 1, 'generated_at_utc': SPOT_TIME, 'reporting_as_of_utc': SPOT_TIME},
            V2_JSON: {'status': 'ok', 'schema_version': 2, 'protocol_id': 'DOGE_FORWARD_RAW_QUOTE_BOUND_V2_20261012_0200', 'audited_at_utc': V2_TIME},
        }
        for name, value in self.sources.items():
            self.write_json(name, value)

    def write(self, name, body):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)

    def write_json(self, name, value):
        self.write(name, (json.dumps(value) + '\n').encode('utf-8'))

    def annotate(self):
        return routing.annotate_legacy_reports(self.root, now=NOW)

    def body(self, name):
        raw = (self.root / name).read_bytes()
        self.assertTrue(raw.startswith(BEGIN))
        self.assertEqual(raw.count(BEGIN), 1)
        self.assertEqual(raw.count(END), 1)
        return raw.split(END, 1)[1]

    def test_distinct_source_cutoffs_and_original_body_bytes_survive(self):
        before_json = {name: (self.root / name).read_bytes() for name in self.sources}
        result = self.annotate()
        self.assertEqual(result['status'], 'ok')
        for name, stamp in ((SPOT, SPOT_TIME), (V2, V2_TIME)):
            with self.subTest(report=name):
                self.assertEqual(self.body(name), self.bodies[name])
                row = result['reports'][name]
                self.assertEqual(row['body_cutoff_utc'], stamp)
                text = (self.root / name).read_bytes().split(END, 1)[0].decode('utf-8')
                self.assertIn(stamp, text)
                self.assertIn('../README.md#current-reports', text)
                self.assertIn('(DOGE_FORWARD_VALIDATION_V2.md)', text)
                self.assertIn('非当前入口', text)
                self.assertNotIn(NOW.isoformat(), text)
        self.assertEqual({name: (self.root / name).read_bytes() for name in self.sources}, before_json)
        self.assertEqual({p.relative_to(self.root).as_posix() for p in self.root.rglob('*') if p.is_file()}, set(self.bodies) | set(self.sources))

    def test_reapplying_notice_is_byte_idempotent(self):
        self.annotate()
        before = {name: (self.root / name).read_bytes() for name in self.bodies}
        result = self.annotate()
        self.assertEqual(before, {name: (self.root / name).read_bytes() for name in self.bodies})
        self.assertTrue(all(not row['changed'] for row in result['reports'].values()))

    def test_regenerated_body_gets_new_notice_without_old_data_time(self):
        self.annotate()
        stamp = '2026-11-01T03:00:00+00:00'
        new_body = ('# regenerated\n截止 UTC：' + stamp + '\n新正文\n').encode('utf-8')
        self.write(V2, new_body)
        self.sources[V2_JSON]['audited_at_utc'] = stamp
        self.write_json(V2_JSON, self.sources[V2_JSON])
        row = self.annotate()['reports'][V2]
        self.assertEqual(self.body(V2), new_body)
        self.assertEqual(row['body_cutoff_utc'], stamp)
        self.assertNotIn(V2_TIME.encode(), (self.root / V2).read_bytes())

    def test_missing_body_is_explicit_and_never_copied_from_other_report(self):
        (self.root / V2).unlink()
        row = self.annotate()['reports'][V2]
        self.assertEqual(row['status'], 'NOT VERIFIED')
        self.assertIn('body_missing', row['reasons'])
        self.assertIsNone(row['body_cutoff_utc'])
        self.assertEqual(self.body(V2), b'')
        self.assertIn('原报告正文缺失', (self.root / V2).read_text(encoding='utf-8'))

    def test_missing_json_keeps_body_and_does_not_borrow_current_report(self):
        (self.root / V2_JSON).unlink()
        self.write_json('data/stage5/forward_validation_v2/latest.json', {'status': 'ok', 'audited_at_utc': NOW.isoformat()})
        result = self.annotate()
        self.assertEqual(result['status'], 'NOT VERIFIED')
        self.assertIn('source_missing', result['reports'][V2]['reasons'])
        self.assertEqual(self.body(V2), self.bodies[V2])
        notice = (self.root / V2).read_bytes().split(END, 1)[0]
        self.assertNotIn(NOW.isoformat().encode(), notice)

    def test_corrupt_json_and_non_object_source_are_degraded(self):
        for body in (b'{bad', b'[]'):
            with self.subTest(body=body):
                self.write(V2_JSON, body)
                row = self.annotate()['reports'][V2]
                self.assertEqual(row['status'], 'NOT VERIFIED')
                self.assertIn('source_invalid_json', row['reasons'])
                self.assertEqual(self.body(V2), self.bodies[V2])

    def test_missing_invalid_naive_future_and_mismatched_times_are_not_verified(self):
        for stamp, reason in ((None, 'source_time_missing'), ('bad', 'source_time_invalid'),
                              ('2026-10-30T11:37:10', 'source_time_invalid'),
                              ('2026-11-12T00:00:00+00:00', 'source_time_future'),
                              ('2026-10-31T00:00:00+00:00', 'body_source_time_mismatch')):
            with self.subTest(stamp=stamp):
                source = dict(self.sources[V2_JSON])
                source['audited_at_utc'] = stamp
                self.write_json(V2_JSON, source)
                row = self.annotate()['reports'][V2]
                self.assertEqual(row['status'], 'NOT VERIFIED')
                self.assertIn(reason, row['reasons'])
                self.assertEqual(self.body(V2), self.bodies[V2])

    def test_json_cutoffs_remain_separate_and_inconsistency_is_visible(self):
        self.sources[SPOT_JSON]['reporting_as_of_utc'] = '2026-10-14T00:00:00+00:00'
        self.write_json(SPOT_JSON, self.sources[SPOT_JSON])
        row = self.annotate()['reports'][SPOT]
        self.assertEqual(row['status'], 'NOT VERIFIED')
        self.assertIn('source_times_inconsistent', row['reasons'])
        text = (self.root / SPOT).read_text(encoding='utf-8')
        self.assertIn('generated_at_utc', text)
        self.assertIn('reporting_as_of_utc', text)
        self.assertIn('2026-10-14T00:00:00+00:00', text)

    def test_missing_or_invalid_body_cutoff_and_future_body_are_degraded(self):
        for body, reason in ((b'# no time\n', 'body_time_missing'),
                             ('截止 UTC：bad\n'.encode(), 'body_time_invalid'),
                             ('截止 UTC：2026-11-12T00:00:00+00:00\n'.encode(), 'body_time_future'),
                             (b'\xff\n', 'body_invalid_utf8')):
            with self.subTest(reason=reason):
                self.write(V2, body)
                row = self.annotate()['reports'][V2]
                self.assertEqual(row['status'], 'NOT VERIFIED')
                self.assertIn(reason, row['reasons'])
                self.assertEqual(self.body(V2), body)

    def test_blocked_or_versionless_source_is_not_verified(self):
        self.sources[V2_JSON].update(status='blocked', protocol_id=None)
        self.write_json(V2_JSON, self.sources[V2_JSON])
        row = self.annotate()['reports'][V2]
        self.assertEqual(row['status'], 'NOT VERIFIED')
        self.assertIn('source_status_not_ok', row['reasons'])
        self.assertIn('source_version_missing', row['reasons'])

    def test_malformed_owned_markers_refuse_all_writes_and_preserve_evidence(self):
        for bad in (BEGIN + b'no end\n', b'prefix\n' + BEGIN + END,
                    BEGIN + BEGIN + END + self.bodies[V2], END + self.bodies[V2]):
            with self.subTest(bad=bad):
                self.write(V2, bad)
                before = {name: (self.root / name).read_bytes() for name in self.bodies}
                with self.assertRaisesRegex(ValueError, 'malformed.*notice'):
                    self.annotate()
                self.assertEqual(before, {name: (self.root / name).read_bytes() for name in self.bodies})

    def test_non_utc_now_is_rejected_without_changes(self):
        with self.assertRaises(ValueError):
            routing.annotate_legacy_reports(self.root, now=datetime(2026, 11, 11))
        self.assertEqual((self.root / V2).read_bytes(), self.bodies[V2])

    def test_cli_only_annotates_beside_its_own_script_and_reports_degradation(self):
        script = self.root / 'report_routing.py'
        script.write_bytes(Path(routing.__file__).read_bytes())
        (self.root / SPOT_JSON).unlink()
        done = subprocess.run([sys.executable, '-I', '-B', str(script)], cwd=self.root.parent,
                              capture_output=True, text=True, encoding='utf-8', check=False)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout)['status'], 'NOT VERIFIED')
        self.assertEqual(self.body(SPOT), self.bodies[SPOT])


if __name__ == '__main__':
    unittest.main()
