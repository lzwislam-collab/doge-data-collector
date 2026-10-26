import importlib
from pathlib import Path
import subprocess
import tempfile
import unittest


class PublicationTests(unittest.TestCase):
    def test_dirty_capture_survives_marker_only_remote_publication(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'writer'
            remote = Path(tmp) / 'remote.git'
            subprocess.run(['git', 'init', '--bare', str(remote)], check=True, capture_output=True)
            root.mkdir()
            def git(*args):
                return subprocess.run(['git', *args], cwd=root, check=True, capture_output=True).stdout
            git('init', '-b', 'main')
            git('config', 'user.name', 'test')
            git('config', 'user.email', 'test@example.invalid')
            git('config', 'core.autocrlf', 'false')
            git('remote', 'add', 'origin', str(remote))
            (root / 'capture').write_bytes(b'old capture\n')
            preparation = root / 'data/pit_retention_preparation_v1/latest.json'
            preparation.parent.mkdir(parents=True)
            preparation.write_bytes(b'{"run_id": "fixture"}\n')
            git('add', '.')
            git('commit', '-m', 'baseline')
            git('push', 'origin', 'main')
            (root / 'capture').write_bytes(b'genuine drained capture\n')
            marker = root / 'data/pit_retention_preparation_v1/artifact_publication.json'
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_bytes(b'{"artifact_id": 123}\n')
            head = git('rev-parse', 'HEAD')
            index = git('write-tree')
            module = importlib.import_module('pit_artifact_publication')
            result = module.publish_marker(root)
            self.assertEqual(result['status'], 'verified_remote_readback')
            git('fetch', 'origin', 'main')
            self.assertEqual(git('show', 'FETCH_HEAD:data/pit_retention_preparation_v1/artifact_publication.json'), marker.read_bytes())
            self.assertEqual(git('show', 'FETCH_HEAD:capture'), b'old capture\n')
            self.assertEqual((root / 'capture').read_bytes(), b'genuine drained capture\n')
            self.assertEqual(git('rev-parse', 'HEAD'), head)
            self.assertEqual(git('write-tree'), index)
            # A later preparation cannot receive this old upload marker.
            remote_head = git('rev-parse', 'FETCH_HEAD')
            preparation.write_bytes(b'{"run_id": "different"}\n')
            with self.assertRaisesRegex(ValueError, 'remote PIT preparation differs'):
                module.publish_marker(root)
            git('fetch', 'origin', 'main')
            self.assertEqual(git('rev-parse', 'FETCH_HEAD'), remote_head)
            self.assertEqual((root / 'capture').read_bytes(), b'genuine drained capture\n')


if __name__ == '__main__':
    unittest.main()
