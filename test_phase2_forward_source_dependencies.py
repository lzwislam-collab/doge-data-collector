"""Verify the offline source-test fallback without network, extraction or writes."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import unittest
import zipfile


ROOT = Path(__file__).resolve().parent
BUNDLE = ROOT / '.test-deps' / 'source-recovery-py.zip'


class SourceTestDependencyTests(unittest.TestCase):
    def isolated(self, body):
        # -I -S excludes installed third-party parsers and ambient PYTHONPATH.
        # Passing an argv list avoids invoking a shell; -B prevents bytecode writes.
        source = 'import sys\nsys.path.append(' + repr(str(ROOT)) + ')\n' + body
        result = subprocess.run([sys.executable, '-I', '-B', '-S', '-c', source],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def test_real_html_css_and_yaml_parsing_without_installed_packages(self):
        self.isolated('''
import importlib.util
from pathlib import Path
assert all(importlib.util.find_spec(name) is None for name in ('bs4', 'soupsieve', 'yaml'))
from source_recovery_test_dependencies import ensure_test_dependencies
before = list(sys.path)
ensure_test_dependencies()
assert sys.path[:-1] == before
assert sys.path[-1].endswith('source-recovery-py.zip')
once = list(sys.path)
ensure_test_dependencies()
assert sys.path == once
import bs4
import soupsieve
import yaml
assert (bs4.__version__, soupsieve.__version__, yaml.__version__) == ('4.12.3', '2.6', '6.0.2')
assert all('source-recovery-py.zip' in module.__file__ for module in (bs4, soupsieve, yaml))
soup = bs4.BeautifulSoup('<table><tr><td class="flow">7.3 &amp; 2</td></tr></table>', 'html.parser')
assert soup.select_one('table tr td.flow').get_text() == '7.3 & 2'
document = yaml.load('on:\\n  schedule:\\n    - cron: "23 1 * * *"\\nflag: false\\nnumber: 7\\n', Loader=yaml.BaseLoader)
assert document == {'on': {'schedule': [{'cron': '23 1 * * *'}]}, 'flag': 'false', 'number': '7'}
assert yaml.__with_libyaml__ is False
print('real HTMLParser + SoupSieve + YAML.BaseLoader PASS; versions 4.12.3 / 2.6 / 6.0.2')
''')

    def test_missing_archive_fails_closed_without_path_or_import_changes(self):
        self.isolated('''
from pathlib import Path
from unittest.mock import patch
from source_recovery_test_dependencies import ensure_test_dependencies
before = list(sys.path)
with patch.object(Path, 'read_bytes', side_effect=FileNotFoundError('missing bundle')):
    try:
        ensure_test_dependencies()
    except ImportError as exc:
        assert 'missing bundle' in str(exc)
    else:
        raise AssertionError('Missing bundle did not fail closed')
assert sys.path == before
assert not any(name in sys.modules for name in ('bs4', 'soupsieve', 'yaml'))
''')

    def test_corrupt_archive_fails_closed_before_parser_import(self):
        self.isolated('''
from pathlib import Path
from unittest.mock import patch
import source_recovery_test_dependencies as helper
bundle = Path(helper.__file__).parent / '.test-deps' / 'source-recovery-py.zip'
content = bundle.read_bytes()
damaged = content[:-1] + bytes([content[-1] ^ 1])
before = list(sys.path)
with patch.object(Path, 'read_bytes', return_value=damaged):
    try:
        helper.ensure_test_dependencies()
    except ImportError as exc:
        assert 'SHA-256 mismatch' in str(exc)
    else:
        raise AssertionError('Damaged bundle did not fail closed')
assert sys.path == before
assert not any(name in sys.modules for name in ('bs4', 'soupsieve', 'yaml'))
''')

    def test_available_packages_are_untouched_and_archive_is_not_read(self):
        self.isolated('''
from pathlib import Path
from unittest.mock import patch
import source_recovery_test_dependencies as helper
bundle = str(Path(helper.__file__).parent / '.test-deps' / 'source-recovery-py.zip')
sys.path.append(bundle)
import bs4
import soupsieve
import yaml
before = list(sys.path)
modules = (bs4, soupsieve, yaml)
with patch.object(Path, 'read_bytes', side_effect=AssertionError('Unneeded bundle read')):
    helper.ensure_test_dependencies()
assert sys.path == before
assert modules == tuple(sys.modules[name] for name in ('bs4', 'soupsieve', 'yaml'))
''')

    def test_archive_allowlist_and_recorded_member_hashes_are_exact(self):
        manifest = json.loads(BUNDLE.with_name('source-recovery-py.provenance.json').read_text(encoding='utf-8'))
        self.assertEqual(hashlib.sha256(BUNDLE.read_bytes()).hexdigest(), manifest['archive_sha256'])
        expected = {member['bundle_member']: member
                    for wheel in manifest['wheels'] for member in wheel['members']}
        with zipfile.ZipFile(BUNDLE) as archive:
            names = archive.namelist()
            self.assertEqual(len(names), len(set(names)))
            self.assertEqual(set(names), set(expected))
            for name in names:
                self.assertTrue(name.startswith(('bs4/', 'soupsieve/', 'yaml/', 'licenses/')), name)
                self.assertTrue(name.endswith('.py') or name.startswith('licenses/'), name)
                self.assertNotIn('..', Path(name).parts)
                self.assertNotIn('tests', Path(name).parts)
                self.assertNotIn('sitecustomize', name)
                content = archive.read(name)
                self.assertEqual(len(content), expected[name]['size'])
                self.assertEqual(hashlib.sha256(content).hexdigest(), expected[name]['sha256'])
        self.assertEqual(sum(name.startswith('licenses/') for name in names), 3)


if __name__ == '__main__':
    unittest.main(verbosity=2)
