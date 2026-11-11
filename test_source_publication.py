"""Publish source data with exact paths and verify the resulting remote bytes."""
import json
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from source_publication import SOURCE_FILES, publish


class SourcePublicationTests(unittest.TestCase):
    def setUp(self):
        base = os.environ.get('P15_TEST_FIXTURE_ROOT')
        self.root = Path(tempfile.mkdtemp(prefix='pub-', dir=base))
        self.remote = self.root/'remote.git'
        self.local = self.root/'local'
        self.git(self.root, 'init', '--bare', str(self.remote))
        self.git(self.remote, 'config', 'core.longpaths', 'true')
        self.git(self.root, 'clone', str(self.remote), str(self.local))
        self.git(self.local, 'config', 'user.name', 'Fixture')
        self.git(self.local, 'config', 'user.email', 'fixture@example.invalid')
        self.git(self.local, 'config', 'core.autocrlf', 'false')
        self.git(self.local, 'checkout', '-b', 'main')
        self.write('data/onchain/latest.json', '{"old":true}\n')
        self.write('data/onchain/daily.csv', 'old\n')
        self.write('data/whales_blocks/pending_block.json', '{"height":1}\n')
        self.git(self.local, 'add', '.')
        self.git(self.local, 'commit', '-m', 'fixture')
        self.git(self.local, 'push', '-u', 'origin', 'main')
    def git(self, root, *args):
        return subprocess.check_output(['git', '-C', str(root), *args], stderr=subprocess.STDOUT)
    def write(self, path, text):
        file=self.local/path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(text.encode('utf-8'))
    def test_publication_preserves_unrelated_dirty_file_and_reads_remote_bytes(self):
        self.write('data/onchain/latest.json', '{"fresh":true}\n')
        self.write('data/onchain/daily.csv', 'fresh\n')
        self.write('unrelated.txt', 'never publish\n')
        result=publish('onchain', self.local, attempts=1)
        self.assertTrue(result['remote_bytes_verified'])
        self.assertEqual(self.git(self.remote, 'show', 'main:data/onchain/latest.json'), b'{"fresh":true}\n')
        self.assertEqual(self.git(self.remote, 'ls-tree', 'main', 'unrelated.txt'), b'')
        self.assertTrue((self.local/'unrelated.txt').exists())
    def test_rejects_unrelated_staged_file(self):
        self.write('unrelated.txt', 'keep\n')
        self.git(self.local, 'add', 'unrelated.txt')
        with self.assertRaisesRegex(ValueError, 'staged'):
            publish('onchain', self.local, attempts=1)
        self.assertEqual(self.git(self.remote, 'show', 'main:data/onchain/daily.csv'), b'old\n')
    def test_completed_pending_deletion_is_published_and_read_back(self):
        for path in SOURCE_FILES['whale_confirmed_blocks']:
            if not path.endswith('pending_block.json'):
                self.write(path, '{}\n')
        (self.local/'data/whales_blocks/pending_block.json').unlink()
        result=publish('whale_confirmed_blocks', self.local, attempts=1)
        self.assertTrue(result['remote_bytes_verified'])
        self.assertIsNone(result['files']['data/whales_blocks/pending_block.json'])
        self.assertEqual(self.git(self.remote, 'ls-tree', 'main', 'data/whales_blocks/pending_block.json'), b'')
    def test_unknown_source_cannot_select_arbitrary_paths(self):
        with self.assertRaises(ValueError):publish('../direction_v42', self.local)
    def test_missing_required_file_is_not_published_as_a_valid_deletion(self):
        (self.local/'data/onchain/daily.csv').unlink()
        with self.assertRaisesRegex(ValueError, 'required'):
            publish('onchain', self.local, attempts=1)
        self.assertEqual(self.git(self.remote,'show','main:data/onchain/daily.csv'), b'old\n')
    def test_existing_unpublished_unrelated_commit_cannot_hitchhike(self):
        self.write('model.txt','out of scope\n')
        self.git(self.local,'add','model.txt')
        self.git(self.local,'commit','-m','unrelated commit')
        with self.assertRaisesRegex(ValueError, 'outgoing'):
            publish('onchain', self.local, attempts=1)
        self.assertEqual(self.git(self.remote,'ls-tree','main','model.txt'),b'')
    def liquid_fixture(self, manifest):
        self.write('data/liquidations/latest.json', json.dumps({'status':'ok','raw_evidence':manifest})+'\n')
        self.write('data/liquidations/events.csv','event_key\n')
    def test_raw_manifest_is_published_and_remote_bytes_verified(self):
        path='data/liquidations/raw/window/websocket.jsonl'
        raw=b'{"kind":"inbound","raw_utf8":"pong"}\n'
        self.write(path,raw.decode())
        self.liquid_fixture([{'path':path,'sha256':hashlib.sha256(raw).hexdigest()}])
        result=publish('liquidations_okx',self.local,attempts=1)
        self.assertIn(path,result['files'])
        self.assertEqual(self.git(self.remote,'show','main:'+path),raw)
    def test_raw_manifest_rejects_hash_mismatch_or_outside_source(self):
        for path in ('data/liquidations/raw/window/missing.json','data/macro/secret.json','../escape.json'):
            with self.subTest(path=path):
                self.liquid_fixture([{'path':path,'sha256':'0'*64}])
                with self.assertRaises(ValueError):publish('liquidations_okx',self.local,attempts=1)
    def test_liquidity_independent_snapshot_reference_is_published(self):
        path='data/liquidity/raw/window/snapshot.json'
        content={'status':'ok','raw_evidence':[]}
        raw=json.dumps(content).encode()
        self.write(path,raw.decode())
        self.write('data/liquidity/latest.json',json.dumps({**content,'snapshot_evidence':{'path':path,'sha256':hashlib.sha256(raw).hexdigest()}}))
        self.write('data/liquidity/hourly_snapshots.csv','collected_at_ms\n')
        result=publish('liquidity',self.local,attempts=1)
        self.assertIn(path,result['files'])
        self.assertEqual(self.git(self.remote,'show','main:'+path),raw)
    def macro_fixture(self):
        self.write('data/macro/latest.json','{"status":"ok"}\n')
        self.write('data/macro/observations.csv','old observations\n')
        self.write('data/macro/revisions.csv','old revisions\n')
        self.git(self.local,'add','data/macro')
        self.git(self.local,'commit','-m','saved macro fixture')
        self.git(self.local,'push','origin','main')
    def test_failed_collection_cannot_publish_a_half_written_history_pair(self):
        self.macro_fixture()
        self.write('data/macro/observations.csv','half-written observations\n')
        self.write('data/macro/latest.json','{"status":"error"}\n')
        with self.assertRaisesRegex(ValueError,'failed collection changed history'):
            publish('macro',self.local,attempts=1)
        self.assertEqual(self.git(self.remote,'show','main:data/macro/observations.csv'),b'old observations\n')
    def test_failed_collection_with_unchanged_history_can_publish_diagnostics(self):
        self.macro_fixture()
        self.write('data/macro/latest.json','{"status":"error"}\n')
        result=publish('macro',self.local,attempts=1)
        self.assertTrue(result['remote_bytes_verified'])
        self.assertEqual(self.git(self.remote,'show','main:data/macro/observations.csv'),b'old observations\n')
    def test_step_failure_cannot_rely_on_old_healthy_metadata(self):
        self.macro_fixture()
        self.write('data/macro/observations.csv','half-written observations\n')
        with self.assertRaisesRegex(ValueError,'failed collection changed history'):
            publish('macro',self.local,attempts=1,collector_outcome='failure')
    def test_reverted_unrelated_outgoing_commits_are_also_rejected(self):
        self.write('data/onchain/daily.csv','unauthorized?\n')
        self.write('model.txt','out of scope\n')
        self.git(self.local,'add','model.txt')
        self.git(self.local,'commit','-m','out of scope')
        self.git(self.local,'rm','model.txt')
        self.git(self.local,'commit','-m','reverted unrelated change')
        with self.assertRaisesRegex(ValueError,'outgoing'):
            publish('onchain',self.local,attempts=1)
    def test_no_change_still_requires_matching_remote_bytes(self):
        result=publish('onchain', self.local, attempts=1)
        self.assertFalse(result['committed'])
        self.assertTrue(result['remote_bytes_verified'])


if __name__=='__main__':unittest.main()
