"""Original public quote and binding checks through fresh, owned Git reads."""
import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import types
import unittest
import uuid
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
REFERENCE = HERE
sys.path.insert(0, str(HERE))
spec = importlib.util.spec_from_file_location('candidate_quote_stream', HERE/'runtime_witness_git_stream.py')
stream = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stream)


def git(root, *args):
    return subprocess.run(['git', *args], cwd=root, check=True, capture_output=True).stdout


def definitions(path, namespace, names):
    namespace = namespace if isinstance(namespace, dict) else namespace.__dict__
    for child in compile(path.read_bytes(), str(path), 'exec', dont_inherit=True).co_consts:
        if isinstance(child, types.CodeType) and child.co_name in names:
            namespace[child.co_name] = types.FunctionType(child, namespace)


class PublicQuoteStreamTests(unittest.TestCase):
    def setUp(self):
        self.root = HERE.parent / 'witness-quote-stream-git-fixtures' / uuid.uuid4().hex
        self.root.mkdir(parents=True)
        git(self.root, 'init', '-q')
        git(self.root, 'config', 'user.name', 'fixture')
        git(self.root, 'config', 'user.email', 'fixture@example.invalid')
        git(self.root, 'config', 'core.autocrlf', 'false')
        git(self.root, 'config', 'core.longpaths', 'true')
        self.raw = '{"original":"α"}\n'.encode('utf-8')
        self.qid = hashlib.sha256(self.raw).hexdigest()
        self.quote_path = 'data/quote_evidence_v2/snapshots/' + self.qid[:2] + '/' + self.qid + '.json'
        self.binding_path = 'data/quote_evidence_v2/bindings.csv'
        for relative, body in ((self.quote_path, self.raw),
                               (self.binding_path, b'forecast_id,prediction_id,kind,quote_id\r\n1,1,ISSUE,q\r\n')):
            path = self.root/relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)
        git(self.root, 'add', '.')
        git(self.root, 'commit', '-qm', 'original immutable public evidence')
        self.head = git(self.root, 'rev-parse', 'HEAD').decode().strip()
        for name in ('forward_validation.py', 'forward_validation_v2.py',
                     'p0_operational_audit.py', 'quote_evidence_v2.py'):
            shutil.copyfile(REFERENCE/name, self.root/name)
        self.base = types.ModuleType('forward_validation')
        self.base.__file__ = str(self.root/'forward_validation.py')
        self.base.subprocess = subprocess
        self.base.REG = 'data/direction_v42/forward_registry.csv'
        self.base.PROTOCOL = 'data/stage5/forward_validation_protocol.json'
        definitions(self.root/'forward_validation.py', self.base, {'git', 'remote_witnesses'})
        self.base.remote_witnesses.__defaults__ = (False,)
        self.original = self.base.git
        self.evidence = types.ModuleType('quote_evidence_v2')
        self.evidence.__file__ = str(self.root/'quote_evidence_v2.py')
        self.evidence.BASE = 'data/quote_evidence_v2'
        self.evidence.git = self.original
        self.evidence.hashlib = hashlib
        definitions(self.root/'quote_evidence_v2.py', self.evidence, {'snapshot_path', 'verify_public_issue'})
        self.namespace = {'__file__': str(self.root/'p0_operational_audit.py'),
                          'base': self.base, 'evidence': self.evidence}
        definitions(self.root/'p0_operational_audit.py', self.namespace, {'main'})
        self.row = {'forecast_id': '1'}
        self.bindings = {'1|ISSUE': {'quote_id': self.qid}}

    def install(self):
        return stream.installed(self.root, self.namespace)

    def verify(self, head=None):
        return self.evidence.verify_public_issue(self.root, self.row, self.bindings, head or self.head)

    def test_original_public_quote_alias_uses_fresh_reads_and_hash_checks(self):
        with self.install() as report:
            self.assertTrue(report['installed'])
            self.assertIsNot(self.evidence.git, self.original)
            self.assertIs(self.evidence.git, self.base.git)
            self.verify()
            self.verify()
            self.assertEqual(report['requests'], 2)
            self.assertEqual(report['verified_reads'], 2)
            self.assertEqual(report['processes'], 1)
            self.assertEqual(report['fallback_calls'], 0)
        self.assertIs(self.evidence.git, self.original)
        self.assertIs(self.base.git, self.original)
        self.assertTrue(report['reaped'])
        self.assertTrue(report['reader_joined'])

    def test_public_binding_csv_keeps_original_text_and_fresh_requests(self):
        expected = self.original(self.root, 'show', self.head + ':' + self.binding_path)
        with self.install() as report:
            self.assertEqual(self.base.git(self.root, 'show', self.head + ':' + self.binding_path), expected)
            self.assertEqual(self.base.git(self.root, 'show', self.head + ':' + self.binding_path), expected)
            self.assertEqual(report['requests'], 2)
            self.assertEqual(report['verified_reads'], 2)

    def test_both_aliases_restore_after_original_validator_error(self):
        error = ValueError('original guard refused publication')
        with self.assertRaises(ValueError) as got:
            with self.install() as report:
                self.verify()
                raise error
        self.assertIs(got.exception, error)
        self.assertIs(self.evidence.git, self.original)
        self.assertIs(self.base.git, self.original)
        self.assertTrue(report['reaped'])

    def test_missing_public_quote_preserves_original_valueerror_and_cause(self):
        bad = '0' * 40
        with self.assertRaises(ValueError) as expected:
            self.verify(bad)
        with self.install() as report:
            with self.assertRaises(ValueError) as got:
                self.verify(bad)
            self.assertEqual(got.exception.args, expected.exception.args)
            old, new = expected.exception.__cause__, got.exception.__cause__
            self.assertIs(type(new), type(old))
            self.assertEqual((new.cmd, new.returncode, new.stdout, new.stderr),
                             (old.cmd, old.returncode, old.stdout, old.stderr))
            self.assertEqual(report['requests'], 1)
            self.assertEqual(report['fallback_calls'], 1)
            self.assertTrue(report['disabled'])
        self.assertIs(self.evidence.git, self.original)

    def test_stream_timeout_closes_before_quote_alias_original_fallback(self):
        with self.install() as report, patch.object(stream.transport._Batch, 'read', side_effect=TimeoutError('request deadline')):
            self.verify()
            self.assertTrue(report['disabled'])
            self.assertTrue(report['reaped'])
            self.assertTrue(report['reader_joined'])
            self.assertEqual(report['fallback_calls'], 1)

    def test_unknown_quote_alias_is_never_replaced(self):
        unknown = lambda *args: 'caller-owned'
        self.evidence.git = unknown
        with self.install() as report:
            self.assertTrue(report['installed'])
            self.assertIs(self.evidence.git, unknown)
            self.assertEqual(report['requests'], 0)
        self.assertIs(self.evidence.git, unknown)

    def test_changed_original_quote_function_is_never_replaced(self):
        self.evidence.verify_public_issue = lambda *args: None
        with self.install() as report:
            self.assertTrue(report['installed'])
            self.assertIs(self.evidence.git, self.original)
        self.assertIs(self.evidence.git, self.original)

    def test_mismatching_quote_shard_stays_on_original_reader(self):
        wrong = '00' if self.qid[:2] != '00' else '11'
        path = 'data/quote_evidence_v2/snapshots/' + wrong + '/' + self.qid + '.json'
        target = self.root/path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(self.raw)
        git(self.root, 'add', path)
        git(self.root, 'commit', '-qm', 'unknown path stays original')
        head = git(self.root, 'rev-parse', 'HEAD').decode().strip()
        with self.install() as report:
            self.assertEqual(self.base.git(self.root, 'show', head + ':' + path), self.raw.decode().strip())
            self.assertEqual(report['requests'], 0)

    def test_real_launcher_imported_alias_and_authenticated_backend(self):
        # Exercise normal repository imports without entering any business main.
        for path in REFERENCE.glob('*.py'):
            if not path.name.startswith('test_'):
                shutil.copyfile(path, self.root/path.name)
        for name in ('runtime_witness_git_stream.py', 'runtime_audit_cli.py'):
            shutil.copyfile(HERE/name, self.root/name)
        code = """import json,runpy,sys
from pathlib import Path
root=Path(sys.argv[1]);sys.path.insert(0,str(root))
import runtime_audit_cli as cli
namespace=runpy.run_path(str(root/'p0_operational_audit.py'),run_name='_owned_quote_test')
namespace=namespace['main'].__globals__
base=namespace['base'];quote=namespace['evidence'];original=quote.git
assert original is base.git
with cli._witness_stream_hook(root,'p0_operational_audit.py',namespace):
 assert quote.git is base.git and quote.git is not original
 quote.verify_public_issue(root,{'forecast_id':'1'},{'1|ISSUE':{'quote_id':sys.argv[3]}},sys.argv[2])
 base.git(root,'show',sys.argv[2]+':data/quote_evidence_v2/bindings.csv')
assert quote.git is original and base.git is original
print('REAL_ALIAS_AND_BACKEND_RESTORED')
"""
        completed = subprocess.run([sys.executable, '-I', '-B', '-c', code,
                                    str(self.root), self.head, self.qid], cwd=self.root,
                                   capture_output=True, timeout=60)
        self.assertEqual(completed.returncode, 0, completed.stderr.decode('utf-8', errors='replace'))
        self.assertEqual(completed.stdout.decode().strip(), 'REAL_ALIAS_AND_BACKEND_RESTORED')
        rows = [json.loads(line) for line in completed.stderr.decode().splitlines() if line.startswith('{')]
        receipt, = [row for row in rows if row.get('kind') == 'runtime_witness_git_stream']
        self.assertEqual(receipt['status'], 'ok')
        self.assertEqual(receipt['requests'], 2)
        self.assertEqual(receipt['verified_reads'], 2)
        self.assertEqual(receipt['fallback_calls'], 0)
        self.assertTrue(receipt['reader_joined'])
        self.assertTrue(receipt['reaped'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
