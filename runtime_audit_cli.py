"""One-shot native history transport for the unchanged serial audit CLIs.

Original imports finish before any hook. Native incompatibility falls back to
the original complete validator; no history/report result is cached or reused.
"""
import argparse
from contextlib import contextmanager, redirect_stdout
import hashlib
import importlib
from pathlib import Path
import runpy
import sys

SCRIPT_BLOBS = {
    'forward_validation_v2.py': '3adbdcc43c5dbdb57ecd482b1d4138e720146ba1',
    'p0_operational_audit.py': 'c615bdc665d24481569f545922d3d7aa04594d60',
    'paired_cohort_audit.py': 'a6bdf0c54ffcf648384c756d400e742ab57a1d61',
}
BACKEND_BLOBS = {
    'runtime_publication_state': 'f0f6b6c7dbe5d7e4c0995b4714a53957217fbac0',
    'runtime_paired_history': 'b27849b2761de895a33613230de9adbf4187df71',
}

def _blob(path):
    raw = path.read_bytes()
    return hashlib.sha1(b'blob '+str(len(raw)).encode('ascii')+b'\0'+raw).hexdigest()

def _notice(backend, reason):
    # Fixed, bounded metadata on stderr keeps each original stdout JSON intact.
    try:
        import json
        print(json.dumps({'kind':'runtime_audit_cli_fallback',
                          'backend':backend,'reason':reason}),file=sys.stderr,flush=True)
    except Exception:
        pass

def _backend(root, name):
    try:
        path = root/(name+'.py')
        if _blob(path) != BACKEND_BLOBS[name]:
            _notice(name,'backend_identity_changed')
            return None
        module = importlib.import_module(name)
        if Path(module.__file__).resolve() != path.resolve():
            _notice(name,'backend_location_changed')
            return None
        return module
    except Exception:
        _notice(name,'backend_import_unavailable')
        return None

def _emit(module, diagnostics, succeeded):
    try:
        with redirect_stdout(sys.stderr):
            module.emit_diagnostics(diagnostics, succeeded)
    except Exception:
        pass

@contextmanager
def _hooks(root, script):
    base = sys.modules.get('forward_validation')
    original = getattr(base,'history_receipts',None)
    publication = _backend(root,'runtime_publication_state') if callable(original) else None
    cohort = paired = previous_pair = None

    def history(*args):
        diagnostics = {}; succeeded = False
        base.history_receipts = original
        try:
            result = publication.history_receipts(*args,diagnostics=diagnostics)
            succeeded = True
            return result
        finally:
            base.history_receipts = history
            _emit(publication,diagnostics,succeeded)

    def pair_history(*args):
        diagnostics = {}; succeeded = False
        cohort.history_guard = previous_pair
        try:
            result = paired.history_guard(*args,cohort=cohort,diagnostics=diagnostics)
            succeeded = True
            return result
        finally:
            cohort.history_guard = pair_history
            _emit(paired,diagnostics,succeeded)

    try:
        if publication is not None:
            base.history_receipts = history
        if script == 'paired_cohort_audit.py':
            cohort = sys.modules.get('paired_forward_cohort')
            previous_pair = getattr(cohort,'history_guard',None)
            paired = _backend(root,'runtime_paired_history') if callable(previous_pair) else None
            if paired is not None:
                cohort.history_guard = pair_history
        yield
    finally:
        if publication is not None:
            base.history_receipts = original
        if paired is not None:
            cohort.history_guard = previous_pair

def run_cli(root, script):
    if script not in SCRIPT_BLOBS:
        raise ValueError('only the three original audit CLIs are allowed')
    root = Path(root).resolve(); path = root/script
    previous_argv = sys.argv; previous_path = list(sys.path)
    sys.argv = [str(path)]; sys.path[:1] = [str(root)]
    try:
        try: known = _blob(path) == SCRIPT_BLOBS[script]
        except OSError: known = False
        if not known:
            _notice('original_cli','script_identity_changed')
            runpy.run_path(str(path),run_name='__main__')
            return None
        # These exact three blobs only enter main via their final __main__ guard.
        namespace = runpy.run_path(str(path),run_name='_doge_owned_audit_cli')
        with _hooks(root,script):
            return namespace['main']()
    finally:
        sys.argv = previous_argv
        sys.path[:] = previous_path

def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('script',choices=tuple(SCRIPT_BLOBS))
    args = parser.parse_args(argv)
    return run_cli(Path(__file__).resolve().parent,args.script)

if __name__ == '__main__':
    raise SystemExit(main())
