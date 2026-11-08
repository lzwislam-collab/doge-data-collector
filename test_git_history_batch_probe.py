"""Real-Git equivalence and fail-closed tests for the offline history probe."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock


SCRIPT = Path(__file__).with_name('git_history_batch_probe.py')


class GitHistoryBatchProbeTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(SCRIPT.is_file(), 'The native Git history probe is not implemented')
        spec = importlib.util.spec_from_file_location('history_batch_probe_under_test', SCRIPT)
        self.probe = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.probe)
        self.temp = tempfile.TemporaryDirectory(prefix='history-probe-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'repo'
        self.root.mkdir()
        self.git('init', '-q', '-b', 'main')
        self.git('config', 'user.name', 'Offline Probe')
        self.git('config', 'user.email', 'probe@example.invalid')
        self.path = 'books.jsonl'

    def git(self, *args, check=True, cwd=None):
        return subprocess.run(['git', *args], cwd=cwd or self.root,
                              capture_output=True, check=check).stdout

    def commit(self, content, message='event'):
        (self.root / self.path).write_bytes(content)
        self.git('add', '--', self.path)
        self.git('commit', '-qm', message)
        return self.git('rev-parse', 'HEAD').decode().strip()

    def baseline(self, oids):
        return {oid: self.git('show', '--format=', '--unified=0', '--no-renames',
                             oid, '--', self.path) for oid in oids}

    def test_all_ordered_patches_match_and_repository_is_unchanged(self):
        for i in range(5):
            self.commit(b''.join(('%s\n' % j).encode() for j in range(i + 1)))
        oids = self.git('log', '--reverse', '--format=%H', '--', self.path).decode().splitlines()
        before = self.git('status', '--porcelain=v1')
        original = self.baseline(oids)
        result = self.probe.run_probe(self.root, self.path, timeout_seconds=20)
        self.assertEqual(result['status'], 'completed')
        self.assertTrue(result['patch_bytes_equal'])
        self.assertTrue(result['ordered_oids_equal'])
        self.assertEqual(result['commit_count'], len(oids))
        self.assertEqual(result['verified_patch_count'], len(oids))
        self.assertEqual(result['total_patch_bytes'], sum(map(len, original.values())))
        manifest = [{'oid': oid, 'patch_bytes': len(original[oid]),
                     'patch_sha256': hashlib.sha256(original[oid]).hexdigest()} for oid in oids]
        expected = hashlib.sha256(json.dumps(manifest, sort_keys=True,
                                              separators=(',', ':')).encode()).hexdigest()
        self.assertEqual(result['patch_manifest_sha256'], expected)
        self.assertEqual(result['serial_seconds'], min(result['serial_before_seconds'],
                                                       result['serial_after_seconds']))
        self.assertEqual(result['fallback_count'], 0)
        self.assertEqual(self.git('status', '--porcelain=v1'), before)
        self.assertEqual(self.baseline(oids), original)

    def test_explicit_reverse_order_and_chunk_boundaries_are_preserved(self):
        oids = [self.commit(b'x\n' * (i + 1)) for i in range(5)]
        requested = list(reversed(oids))
        original = self.baseline(requested)
        batch, stats = self.probe.read_batch(self.root, self.path, requested, original,
                                            timeout_seconds=20, chunk_size=2)
        self.assertEqual(list(batch), requested)
        self.assertEqual(batch, original)
        self.assertEqual(stats['batch_show_count'], 3)
        self.assertEqual(stats['returned_batch_count'], 5)

    def test_empty_commit_is_explicitly_verified_by_original_show_fallback(self):
        a = self.commit(b'first\n')
        self.git('commit', '--allow-empty', '-qm', 'empty')
        empty = self.git('rev-parse', 'HEAD').decode().strip()
        b = self.commit(b'first\nsecond\n')
        oids = [a, empty, b]
        original = self.baseline(oids)
        self.assertEqual(original[empty], b'')
        batch, stats = self.probe.read_batch(self.root, self.path, oids, original,
                                            timeout_seconds=20)
        self.assertEqual(list(batch), oids)
        self.assertEqual(batch, original)
        self.assertEqual(stats['fallback_count'], 1)
        self.assertEqual(stats['returned_batch_count'], 2)

    def test_merge_combined_diff_is_byte_identical(self):
        self.commit(b'base\n')
        self.git('checkout', '-qb', 'side')
        self.commit(b'side\n')
        self.git('checkout', '-q', 'main')
        self.commit(b'main\n')
        self.git('merge', '--no-ff', 'side', check=False)
        merge = self.commit(b'resolved\n', 'merge resolution')
        self.assertIn(b'diff --cc ', self.baseline([merge])[merge])
        result = self.probe.run_probe(self.root, self.path, timeout_seconds=20)
        self.assertEqual(result['commit_count'], 4)
        self.assertTrue(result['patch_bytes_equal'])

    def test_nul_crlf_missing_newline_and_nondefault_diff_config_are_preserved(self):
        (self.root / '.gitattributes').write_text('books.jsonl diff\n')
        self.git('add', '.gitattributes')
        self.git('config', 'diff.algorithm', 'histogram')
        self.git('config', 'core.quotePath', 'false')
        self.git('config', 'color.ui', 'always')
        self.commit(b'header\r\n\x00DPP-HISTORY:not-a-frame\x00\nlast')
        self.commit(b'header\r\n\x00DPP-HISTORY:not-a-frame\x00\nlast\r\nadded')
        result = self.probe.run_probe(self.root, self.path, timeout_seconds=20)
        self.assertEqual(result['commit_count'], 2)
        self.assertTrue(result['patch_bytes_equal'])

    def test_corrupt_missing_duplicate_or_reordered_frames_are_rejected(self):
        oids = [self.commit(b'a\n'), self.commit(b'a\nb\n')]
        original = self.baseline(oids)
        marker = b'\x00DPP-HISTORY:test:'
        def frame(oid, patch):
            return marker + oid.encode() + b'\x00\n\n' + patch
        good = b''.join(frame(oid, original[oid]) for oid in oids)
        for bad in [good + b'extra', frame(oids[1], original[oids[1]]),
                    frame(oids[0], original[oids[0]]) * 2,
                    b''.join(frame(oid, original[oid]) for oid in reversed(oids)),
                    good[:-1], good.replace(b'\x00\n\n', b'\x00bad', 1)]:
            with self.subTest(bad=bad[:35]):
                with self.assertRaises(self.probe.ProbeError):
                    self.probe.verify_batch_output(bad, marker, oids, original)

    def test_empty_history_invalid_oid_and_marker_collision_are_rejected(self):
        oid = self.commit(b'a\n')
        with self.assertRaises(self.probe.ProbeError):
            self.probe.run_probe(self.root, 'absent.jsonl', timeout_seconds=20)
        for invalid in [[], [oid, oid], ['HEAD'], [oid + '^'], ['0' * 40]]:
            with self.subTest(invalid=invalid):
                with self.assertRaises(self.probe.ProbeError):
                    self.probe.read_batch(self.root, self.path, invalid,
                                          {value: b'patch' for value in invalid}, timeout_seconds=20)
        marker = b'\x00DPP-HISTORY:collision:'
        with self.assertRaises(self.probe.ProbeError):
            self.probe.verify_batch_output(b'', marker, [oid], {oid: marker})

    def test_shallow_replace_and_external_diff_are_rejected(self):
        first = self.commit(b'a\n')
        last = self.commit(b'a\nb\n')
        shallow = Path(self.temp.name) / 'shallow'
        self.git('clone', '-q', '--depth=1', self.root.as_uri(), str(shallow))
        with self.assertRaisesRegex(self.probe.ProbeError, 'shallow'):
            self.probe.run_probe(shallow, self.path, timeout_seconds=20)
        self.git('replace', last, first)
        with self.assertRaisesRegex(self.probe.ProbeError, 'replace'):
            self.probe.run_probe(self.root, self.path, timeout_seconds=20)
        self.git('replace', '-d', last)
        self.git('config', 'diff.custom.textconv', 'unapproved-command')
        with self.assertRaisesRegex(self.probe.ProbeError, 'textconv|external'):
            self.probe.run_probe(self.root, self.path, timeout_seconds=20)

    def test_shared_clone_requires_exact_explicit_object_directory(self):
        self.commit(b'a\n')
        self.commit(b'a\nb\n')
        shared = Path(self.temp.name) / 'shared'
        self.git('clone', '-q', '--shared', '--local', str(self.root), str(shared))
        trusted = self.root / '.git' / 'objects'
        with self.assertRaisesRegex(self.probe.ProbeError, 'alternates|object'):
            self.probe.run_probe(shared, self.path, timeout_seconds=20)
        result = self.probe.run_probe(shared, self.path, timeout_seconds=20,
                                      allow_object_directory=trusted)
        self.assertEqual(result['commit_count'], 2)
        self.assertTrue(result['patch_bytes_equal'])
        with self.assertRaisesRegex(self.probe.ProbeError, 'alternates|object'):
            self.probe.run_probe(shared, self.path, timeout_seconds=20,
                                  allow_object_directory=shared / '.git' / 'objects')

    def test_same_count_history_change_is_rejected(self):
        self.commit(b'a\n')
        self.commit(b'a\nb\n')
        real_batch = self.probe.read_batch
        def change_after_batch(*args, **kwargs):
            result = real_batch(*args, **kwargs)
            self.git('commit', '--amend', '-qm', 'same count, different oid')
            return result
        with mock.patch.object(self.probe, 'read_batch', side_effect=change_after_batch):
            with self.assertRaisesRegex(self.probe.ProbeError, 'changed'):
                self.probe.run_probe(self.root, self.path, timeout_seconds=20)

    def test_timeout_and_bad_path_fail_closed(self):
        self.commit(b'a\n')
        for timeout in [0, -1, float('nan'), float('inf')]:
            with self.subTest(timeout=timeout):
                with self.assertRaises((ValueError, self.probe.ProbeError)):
                    self.probe.run_probe(self.root, self.path, timeout_seconds=timeout)
        with self.assertRaises(self.probe.ProbeTimeout):
            self.probe.run_probe(self.root, self.path, timeout_seconds=1e-9)
        for path in ['../books.jsonl', '/books.jsonl', ':(glob)*', 'books*.jsonl']:
            with self.subTest(path=path):
                with self.assertRaises(self.probe.ProbeError):
                    self.probe.run_probe(self.root, path, timeout_seconds=20)


if __name__ == '__main__':
    unittest.main()
