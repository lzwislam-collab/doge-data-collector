"""Offline parser fallback used only by the source recovery test modules.

Production collectors never import this helper. Installed packages retain
precedence; the pinned, pure-Python archive is appended only when needed.
"""
import hashlib
import importlib
import importlib.util
import io
from pathlib import Path, PurePosixPath
import stat
import sys
import zipfile

_BUNDLE_SHA256 = 'cbc8ab33550f642cdfd3ba5b2b0e85a16b9ce4ddf5f945ba20d6bd24ce729c51'
_BUNDLE_MEMBERS = frozenset(('bs4/__init__.py', 'bs4/builder/__init__.py', 'bs4/builder/_html5lib.py', 'bs4/builder/_htmlparser.py', 'bs4/builder/_lxml.py', 'bs4/css.py', 'bs4/dammit.py', 'bs4/diagnose.py', 'bs4/element.py', 'bs4/formatter.py', 'licenses/PyYAML.txt', 'licenses/beautifulsoup4.txt', 'licenses/soupsieve.txt', 'soupsieve/__init__.py', 'soupsieve/__meta__.py', 'soupsieve/css_match.py', 'soupsieve/css_parser.py', 'soupsieve/css_types.py', 'soupsieve/pretty.py', 'soupsieve/util.py', 'yaml/__init__.py', 'yaml/composer.py', 'yaml/constructor.py', 'yaml/cyaml.py', 'yaml/dumper.py', 'yaml/emitter.py', 'yaml/error.py', 'yaml/events.py', 'yaml/loader.py', 'yaml/nodes.py', 'yaml/parser.py', 'yaml/reader.py', 'yaml/representer.py', 'yaml/resolver.py', 'yaml/scanner.py', 'yaml/serializer.py', 'yaml/tokens.py'))


def ensure_test_dependencies():
    """Make absent test parsers available or fail closed with ImportError.

    No network, installer, extraction, package replacement, or file writes occur.
    """
    if all(importlib.util.find_spec(name) is not None for name in ('bs4', 'soupsieve', 'yaml')):
        return
    bundle = Path(__file__).resolve().parent / '.test-deps' / 'source-recovery-py.zip'
    try:
        content = bundle.read_bytes()
        if hashlib.sha256(content).hexdigest() != _BUNDLE_SHA256:
            raise ValueError('archive SHA-256 mismatch')
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            entries = archive.infolist()
            names = [entry.filename for entry in entries]
            if len(names) != len(set(names)) or set(names) != _BUNDLE_MEMBERS:
                raise ValueError('archive members differ from the exact allowlist')
            for entry in entries:
                path = PurePosixPath(entry.filename)
                if (entry.is_dir() or path.is_absolute() or
                        any(part in ('', '.', '..') for part in path.parts) or
                        '\\' in entry.filename or ':' in entry.filename or
                        stat.S_ISLNK(entry.external_attr >> 16)):
                    raise ValueError('unsafe archive member')
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        raise ImportError('Source recovery test dependencies unavailable: ' + str(exc)) from exc
    location = str(bundle)
    added = location not in sys.path
    if added:
        sys.path.append(location)
    importlib.invalidate_caches()
    try:
        for name in ('bs4', 'soupsieve', 'yaml'):
            if importlib.util.find_spec(name) is None:
                raise ImportError('Source recovery test dependency missing: ' + name)
    except Exception:
        if added:
            sys.path.remove(location)
        raise
