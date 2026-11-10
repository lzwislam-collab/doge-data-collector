"""One-shot native history transport for the unchanged serial audit CLIs.

Original imports finish before any hook. Native incompatibility falls back to
the original complete validator; no history/report result is cached or reused.
"""
import argparse
from collections import OrderedDict
from contextlib import contextmanager, redirect_stdout
import hashlib
import importlib
import json
import marshal
from pathlib import Path
import runpy
import sys
import types

_STANDARD_JSON_DUMPS = json.dumps


def _namespace_state(namespace, excluded=()):
    return tuple((key, value) for key, value in namespace.items() if key not in excluded)


def _same_namespace(namespace, state, excluded=()):
    return (len(namespace) - sum(key in namespace for key in excluded) == len(state)
            and all(key in namespace and namespace[key] is value for key, value in state))


def _standard_json_state():
    """Capture the actual standard encoder chain before installing any hooks."""
    import _json
    encoder = json.encoder
    if not (encoder.c_make_encoder is _json.make_encoder
            and encoder.encode_basestring_ascii is _json.encode_basestring_ascii
            and isinstance(encoder.encode_basestring_ascii, types.BuiltinFunctionType)
            and type(encoder.c_make_encoder) is type
            and encoder.c_make_encoder.__module__ == '_json'
            and json.JSONEncoder.__module__ == 'json.encoder'
            and json.JSONEncoder.__name__ == 'JSONEncoder'):
        return None
    for namespace, filename in ((json.__dict__, json.__file__),
                                (encoder.__dict__, encoder.__file__),
                                (json.JSONEncoder.__dict__, encoder.__file__)):
        if any(Path(value.__code__.co_filename).resolve() != Path(filename).resolve()
               for value in namespace.values() if isinstance(value, types.FunctionType)):
            return None
    namespaces = ((json.__dict__, _namespace_state(json.__dict__, ('dumps',)), ('dumps',)),
                  (encoder.__dict__, _namespace_state(encoder.__dict__), ()),
                  (json.JSONEncoder.__dict__, _namespace_state(json.JSONEncoder.__dict__), ()),
                  (json._default_encoder.__dict__, _namespace_state(json._default_encoder.__dict__), ()),
                  (_json.__dict__, _namespace_state(_json.__dict__), ()))
    functions = {}
    for namespace, _, _ in namespaces:
        for value in namespace.values():
            if isinstance(value, types.FunctionType):
                functions[id(value)] = (value, value.__code__, value.__defaults__,
                    None if value.__kwdefaults__ is None else _namespace_state(value.__kwdefaults__))
    return namespaces, tuple(functions.values())


try:
    _STANDARD_JSON_STATE = _standard_json_state()
except Exception:
    _STANDARD_JSON_STATE = None


def _standard_json_unchanged():
    """Any changed encoder code/default/class/global uses the complete original."""
    if json.dumps is not _STANDARD_JSON_DUMPS or _STANDARD_JSON_STATE is None:
        return False
    try:
        namespaces, functions = _STANDARD_JSON_STATE
        if not all(_same_namespace(namespace, state, excluded)
                   for namespace, state, excluded in namespaces):
            return False
        for function, code, defaults, keywords in functions:
            if function.__code__ is not code or function.__defaults__ is not defaults:
                return False
            actual = function.__kwdefaults__
            if ((keywords is None and actual is not None) or
                    (keywords is not None and (actual is None or not _same_namespace(actual, keywords)))):
                return False
        return True
    except Exception:
        return False

SCRIPT_BLOBS = {
    'forward_validation_v2.py': '3adbdcc43c5dbdb57ecd482b1d4138e720146ba1',
    'p0_operational_audit.py': 'c615bdc665d24481569f545922d3d7aa04594d60',
    'paired_cohort_audit.py': 'a6bdf0c54ffcf648384c756d400e742ab57a1d61',
}
BACKEND_BLOBS = {
    'runtime_publication_state': 'f0f6b6c7dbe5d7e4c0995b4714a53957217fbac0',
    'runtime_paired_history': 'b27849b2761de895a33613230de9adbf4187df71',
    'runtime_p0_inventory_transport': '139cde4b032f50ec83fb1a35dac3ff0856147d70',
}
CANONICAL_BLOBS = {
    'paired_forward_cohort.py': '02e6b3ba51f063ba17cddcb98ff6445a08235c97',
    'forward_execution_evidence.py': '7c2fd41af2fc5e52afd7fcabe2ea909b9457b7bb',
    'paired_cohort_audit.py': SCRIPT_BLOBS['paired_cohort_audit.py'],
}


def _json_closed(value):
    """Eligibility only: unknown, cyclic or very deep values use original JSON."""
    active = set(); remaining = [1000000]
    def visit(item, depth):
        remaining[0] -= 1
        if remaining[0] < 0 or depth > 100: return False
        kind = type(item)
        if kind in (str, int, float, bool, type(None)): return True
        if kind not in (dict, list, tuple) or id(item) in active: return False
        active.add(id(item))
        try:
            if kind is dict:
                return all(type(k) in (str,int,float,bool,type(None)) and visit(v,depth+1)
                           for k,v in item.items())
            return all(visit(v,depth+1) for v in item)
        finally:
            active.remove(id(item))
    return visit(value,0)


class _CanonicalBytes:
    """Per-CLI LRU of exact canonical bytes, never digests or validation results."""
    def __init__(self, original, max_bytes=64*1024*1024, max_entries=128):
        self.original = original; self.max_bytes = max_bytes; self.max_entries = max_entries
        self.entries = OrderedDict()
        self.stats = dict(calls=0,hits=0,misses=0,fallback_calls=0,sha256_calls=0,
                          encoded_calls=0,evictions=0,entries=0,cached_bytes=0)

    def _fallback(self, value):
        self.stats['fallback_calls'] += 1
        result = self.original(value)
        # The pinned original executes exactly one SHA for every successful call.
        self.stats['sha256_calls'] += 1
        return result

    def digest(self, value):
        self.stats['calls'] += 1
        if not _standard_json_unchanged(): return self._fallback(value)
        try: key = marshal.dumps(value,2)
        except Exception: return self._fallback(value)
        if not _standard_json_unchanged(): return self._fallback(value)
        if len(key) > self.max_bytes: return self._fallback(value)
        canonical = self.entries.get(key)
        if canonical is not None:
            # marshal has type tags and rejects builtin subclasses. An exact hit
            # reconstructs a previously checked builtin JSON graph; no id is kept.
            self.entries.move_to_end(key); self.stats['hits'] += 1
        else:
            self.stats['misses'] += 1
            try:
                # Only decode our own bounded, successfully marshalled bytes.
                # Unknown buffer exporters decode as bytes and fail JSON, so
                # np.float64/bytes collisions cannot seed a canonical cache hit.
                snapshot = marshal.loads(key)
            except Exception: return self._fallback(value)
            if not _standard_json_unchanged(): return self._fallback(value)
            try:
                self.stats['encoded_calls'] += 1
                canonical = json.dumps(snapshot,sort_keys=True,
                    separators=(',', ':'),allow_nan=False).encode()
            except Exception: return self._fallback(value)
            if not _standard_json_unchanged(): return self._fallback(value)
            size = len(key)+len(canonical)
            if size > self.max_bytes: return self._fallback(value)
            # A signal/thread mutation during a miss must never associate bytes
            # from different object states. Recheck only before storing the miss.
            try: unchanged = (marshal.dumps(value,2) == key and
                              marshal.dumps(snapshot,2) == key)
            except Exception: unchanged = False
            if not unchanged or not _standard_json_unchanged(): return self._fallback(value)
            if unchanged:
                while self.entries and (len(self.entries)>=self.max_entries or
                        self.stats['cached_bytes']+size>self.max_bytes):
                    old_key,old_value = self.entries.popitem(last=False)
                    self.stats['cached_bytes'] -= len(old_key)+len(old_value)
                    self.stats['evictions'] += 1
                if self.max_entries > 0:
                    self.entries[key] = canonical; self.stats['cached_bytes'] += size
                    self.stats['entries'] = len(self.entries)
        self.stats['sha256_calls'] += 1
        return hashlib.sha256(canonical).hexdigest()

    def clear(self):
        self.entries.clear(); self.stats['entries'] = 0; self.stats['cached_bytes'] = 0


@contextmanager
def _canonical_hook(root, script, cohort):
    original = getattr(cohort,'digest',None); depth = getattr(cohort,'depth',None)
    eligible = False
    try:
        eligible = (script == 'paired_cohort_audit.py' and callable(original)
            and all(_blob(root/path)==oid for path,oid in CANONICAL_BLOBS.items())
            and Path(cohort.__file__).resolve()==(root/'paired_forward_cohort.py').resolve()
            and Path(depth.__file__).resolve()==(root/'forward_execution_evidence.py').resolve()
            and Path(original.__code__.co_filename).resolve()==(root/'paired_forward_cohort.py').resolve()
            and original.__globals__.get('depth') is depth
            and Path(depth.digest.__code__.co_filename).resolve()==(root/'forward_execution_evidence.py').resolve()
            and depth.digest.__globals__.get('json') is json
            and depth.digest.__globals__.get('hashlib') is hashlib)
    except Exception:
        pass
    if not eligible:
        yield None
        return
    cache = _CanonicalBytes(original); succeeded = False
    cohort.digest = cache.digest
    try:
        yield cache
        succeeded = True
    finally:
        cohort.digest = original
        stats = dict(cache.stats); cache.clear()
        try:
            print(json.dumps(dict(stats,kind='runtime_canonical_json_transport',
                implementation='paired_canonical_utf8_lru_v1',mode='canonical_bytes_lru',
                status='ok' if succeeded else 'error',
                module_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()),
                sort_keys=True),file=sys.stderr,flush=True)
        except Exception:
            pass

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
        with _canonical_hook(root,script,cohort):
            yield
    finally:
        if publication is not None:
            base.history_receipts = original
        if paired is not None:
            cohort.history_guard = previous_pair

@contextmanager
def _p0_hook(root, script, main_globals):
    """Install fresh bounded GET transport only in the original P0 main globals."""
    if script != 'p0_operational_audit.py':
        yield
        return
    module = _backend(root, 'runtime_p0_inventory_transport')
    if module is None:
        yield
        return
    succeeded = False
    with module.installed(main_globals) as report:
        try:
            yield
            succeeded = True
        finally:
            try:
                print(json.dumps(dict(report, kind='runtime_p0_inventory_transport',
                    mode='fresh_bounded_inventory', status='ok' if succeeded else 'error',
                    module_sha256=hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()),
                    sort_keys=True), file=sys.stderr, flush=True)
            except Exception:
                pass

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
        with _hooks(root,script), _p0_hook(root,script,namespace['main'].__globals__):
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
