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
import os
from pathlib import Path
import runpy
import sys
import types

SCRIPT_BLOBS = {
    'forward_validation_v2.py': '3adbdcc43c5dbdb57ecd482b1d4138e720146ba1',
    'p0_operational_audit.py': 'db494c83ddfbb47662a864b1764ad42283f6a0f1',
    'paired_cohort_audit.py': 'a6bdf0c54ffcf648384c756d400e742ab57a1d61',
}
BACKEND_BLOBS = {
    'runtime_publication_state': 'f0f6b6c7dbe5d7e4c0995b4714a53957217fbac0',
    'runtime_paired_history': 'b27849b2761de895a33613230de9adbf4187df71',
    'runtime_p0_inventory_transport': '1b612100f3366f456018e3ef834dd0e425810349',
    'runtime_paired_git_stream': 'b4760557613e8753ca9dffcc0770a83833591240',
    'runtime_witness_git_stream': 'cfe31f0fc512bc6632d8c084e5aa1dd576ab73be',
}
CANONICAL_BLOBS = {
    'paired_forward_cohort.py': '02e6b3ba51f063ba17cddcb98ff6445a08235c97',
    'forward_execution_evidence.py': '7c2fd41af2fc5e52afd7fcabe2ea909b9457b7bb',
    'paired_cohort_audit.py': SCRIPT_BLOBS['paired_cohort_audit.py'],
}


def _json_closed(value):
    """Eligibility only: unknown, cyclic or very deep values use original JSON."""
    active = set(); remaining = 1000000
    atomic = (str, int, float, bool, type(None))
    containers = (dict, list, tuple)
    end = object(); stack = []
    iterator, depth, container_id, dictionary = iter((value,)), 0, None, False
    while True:
        item = next(iterator, end)
        if item is end:
            if container_id is not None: active.remove(container_id)
            if not stack: return True
            iterator, depth, container_id, dictionary = stack.pop()
            continue
        if dictionary:
            key, item = item
            if type(key) not in atomic: return False
        remaining -= 1
        if remaining < 0 or depth > 100: return False
        kind = type(item)
        if kind in atomic: continue
        if kind not in containers or id(item) in active: return False
        child_id = id(item); active.add(child_id)
        stack.append((iterator, depth, container_id, dictionary))
        dictionary = kind is dict
        iterator = iter(item.items()) if dictionary else iter(item)
        depth += 1; container_id = child_id


def _one_hour_fit(value):
    """Cost routing only; the original digest still validates every value.

    The frozen examples label h candles after the anchor candle closes, so a
    one-hour fit has label_close_ms-anchor_ms == two hours. Unknown shapes use
    the existing cache path. No identity or qualification is granted here.
    """
    if type(value) is not dict or len(value) != 3 or any(type(k) is not str for k in value):
        return False
    if set(value) != {'train','calibration','validation'}:
        return False
    for group in ('train','calibration','validation'):
        rows=value[group]
        if type(rows) is not list or not rows or type(rows[0]) is not dict:
            return False
        row=rows[0]
        if any(type(k) is not str for k in row):return False
        anchor,close=row.get('anchor_ms'),row.get('label_close_ms')
        if type(anchor) is not int or type(close) is not int or close-anchor != 7200000:
            return False
    return True


class _CanonicalBytes:
    """Per-CLI LRU of exact canonical bytes, never digests or validation results."""
    def __init__(self, original, max_bytes=64*1024*1024, max_entries=128):
        self.original = original; self.max_bytes = max_bytes; self.max_entries = max_entries
        self.entries = OrderedDict()
        self.stats = dict(calls=0,hits=0,misses=0,direct_calls=0,fallback_calls=0,sha256_calls=0,
                          encoded_calls=0,evictions=0,entries=0,cached_bytes=0)

    def _fallback(self, value):
        self.stats['fallback_calls'] += 1
        result = self.original(value)
        # The pinned original executes exactly one SHA for every successful call.
        self.stats['sha256_calls'] += 1
        return result

    def digest(self, value):
        self.stats['calls'] += 1
        try: direct = _one_hour_fit(value)
        except Exception: return self._fallback(value)
        if direct:
            # These real fit values were miss-only in the complete archived
            # trace. Keep the exact original encoder/error/fresh SHA behavior,
            # without first serializing and walking a cache key that is unused.
            self.stats['direct_calls'] += 1
            result = self.original(value)
            self.stats['sha256_calls'] += 1
            return result
        try: key = marshal.dumps(value,2)
        except Exception: return self._fallback(value)
        if len(key) > self.max_bytes: return self._fallback(value)
        canonical = self.entries.get(key)
        if canonical is not None:
            # Typed key bytes match a previously checked JSON graph;
            # its exact canonical bytes are reused without retaining object IDs.
            self.entries.move_to_end(key); self.stats['hits'] += 1
        else:
            # A large uncached typed key consumes more than a fair share of
            # the original bounded LRU. Use the complete original encoder and
            # fresh SHA instead of paying a graph walk and a second key dump
            # before promptly evicting these measured large miss-only values.
            # Small receipts retain their original cache and all byte bounds.
            if len(key) > max(512*1024,self.max_bytes//max(1,self.max_entries)):
                self.stats['direct_calls'] += 1
                result = self.original(value)
                self.stats['sha256_calls'] += 1
                return result
            self.stats['misses'] += 1
            try: closed = _json_closed(value)
            except Exception: return self._fallback(value)
            if not closed: return self._fallback(value)
            self.stats['encoded_calls'] += 1
            canonical = json.dumps(value,sort_keys=True,separators=(',', ':'),allow_nan=False).encode()
            size = len(key)+len(canonical)
            if size > self.max_bytes: return self._fallback(value)
            # A signal/thread mutation during a miss must never associate bytes
            # from different object states. Recheck only before storing the miss.
            try: unchanged = marshal.dumps(value,2) == key
            except Exception: unchanged = False
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
                implementation='paired_canonical_utf8_lru_v2_fit1h_direct',mode='canonical_bytes_lru',
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


def _paired_original_context(root,script,namespace):
    """Reject synthetic/AST contexts silently before importing a new backend."""
    if script!='paired_cohort_audit.py' or not isinstance(namespace,dict):return False
    try:
        path=root/script
        if path.stat().st_size>1024*1024 or _blob(path)!='a6bdf0c54ffcf648384c756d400e742ab57a1d61':return False
        cohort=namespace['cohort'];publication=cohort.publication
        for filename,module,oid in (
                ('paired_forward_cohort.py',cohort,'02e6b3ba51f063ba17cddcb98ff6445a08235c97'),
                ('publish_forward_evidence.py',publication,'d982f4f8bd278d7a1505073bc35dd71b80eeb095')):
            if (type(module) is not types.ModuleType or Path(module.__file__).resolve()!=root/filename
                    or (root/filename).stat().st_size>1024*1024 or _blob(root/filename)!=oid):return False
        code=compile(path.read_bytes(),str(path),'exec',dont_inherit=True)
        children=[child for child in code.co_consts if isinstance(child,types.CodeType)]
        if len(children)!=6 or {child.co_name for child in children}!={'nonoverlap','metrics','bounded_cache','verify_pair','evaluate','main'}:return False
        special={'__name__','__doc__','__package__','__loader__','__spec__','__file__','__cached__','__builtins__'}
        if set(namespace)-set(code.co_names)-special:return False
        for child in children:
            function=namespace.get(child.co_name)
            defaults={'verify_pair':(None,),'evaluate':(cohort.ROOT,None)}.get(child.co_name)
            actual_defaults=function.__defaults__ if isinstance(function,types.FunctionType) else None
            same_defaults=(actual_defaults is None if defaults is None else
                type(actual_defaults) is tuple and len(actual_defaults)==len(defaults)
                and all(actual is expected for actual,expected in zip(actual_defaults,defaults)))
            if (not isinstance(function,types.FunctionType) or function.__code__!=child
                    or function.__globals__ is not namespace or not same_defaults
                    or function.__kwdefaults__ is not None or function.__closure__ is not None):return False
        if (type(cohort.ROOT) is not type(root) or Path(cohort.ROOT).resolve()!=root or Path(namespace['__file__']).resolve()!=path
                or namespace.get('json') is not json or namespace.get('hashlib') is not hashlib
                or namespace.get('OrderedDict') is not OrderedDict):return False
        for key in ('selection','quotes','frozen'):
            if namespace.get(key) is not getattr(cohort,key,None):return False
        pubcode=compile((root/'publish_forward_evidence.py').read_bytes(),str(root/'publish_forward_evidence.py'),'exec',dont_inherit=True)
        commands=[child for child in pubcode.co_consts if isinstance(child,types.CodeType) and child.co_name=='command']
        command=publication.command
        return (len(commands)==1 and isinstance(command,types.FunctionType) and command.__code__==commands[0]
                and command.__globals__ is publication.__dict__ and command.__defaults__ is None
                and command.__kwdefaults__ is None and command.__closure__ is None)
    except (AttributeError,KeyError,OSError,TypeError,ValueError,SyntaxError):return False


@contextmanager
def _paired_stream_hook(root,script,main_globals):
    if not _paired_original_context(root,script,main_globals):
        yield;return
    module=_backend(root,'runtime_paired_git_stream')
    if module is None:
        yield;return
    report=None;succeeded=False
    try:
        with module.installed(root,main_globals,trusted_object_directory=
                os.environ.get('DOGE_AUDIT_STREAM_TRUSTED_OBJECT_DIRECTORY')) as report:
            yield
            succeeded=True
    finally:
        if report is not None and report.get('installed') is True:
            try:
                fields={key:report[key] for key in ('installed','implementation','requests','verified_reads',
                    'processes','fallback_calls','reaped','reader_joined','disabled')}
                print(json.dumps(dict(fields,kind='runtime_paired_git_stream',mode='fresh_sequential_blobs',
                    status='ok' if succeeded else 'error',module_sha256=hashlib.sha256(
                        Path(module.__file__).read_bytes()).hexdigest()),sort_keys=True),file=sys.stderr,flush=True)
            except Exception:pass

@contextmanager
def _witness_stream_hook(root,script,main_globals):
    if script not in ('forward_validation_v2.py','p0_operational_audit.py'):
        yield;return
    # Synthetic/AST launcher tests and unknown CLIs must keep the original path.
    expected={'forward_validation_v2.py':'3adbdcc43c5dbdb57ecd482b1d4138e720146ba1',
              'p0_operational_audit.py':'db494c83ddfbb47662a864b1764ad42283f6a0f1'}
    try: known=(root/script).stat().st_size<=1024*1024 and _blob(root/script)==expected[script]
    except (OSError,ValueError): known=False
    if not known:
        yield;return
    module=_backend(root,'runtime_witness_git_stream')
    peer=_backend(root,'runtime_paired_git_stream') if module is not None else None
    if module is None or peer is None or module.transport is not peer:
        yield;return
    report=None;succeeded=False
    try:
        with module.installed(root,main_globals,trusted_object_directory=
                os.environ.get('DOGE_AUDIT_STREAM_TRUSTED_OBJECT_DIRECTORY')) as report:
            yield
            succeeded=True
    finally:
        if report is not None:
            try:
                fields={key:report[key] for key in ('installed','implementation','requests','verified_reads',
                    'processes','fallback_calls','reaped','reader_joined','disabled')}
                status=('error' if not succeeded else 'ok' if report['installed'] and
                        not report['disabled'] and not report['fallback_calls'] else 'fallback')
                print(json.dumps(dict(fields,kind='runtime_witness_git_stream',mode='fresh_sequential_blobs',
                    status=status,module_sha256=hashlib.sha256(
                        Path(module.__file__).read_bytes()).hexdigest()),sort_keys=True),file=sys.stderr,flush=True)
            except Exception:pass


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
        with _hooks(root,script), _p0_hook(root,script,namespace['main'].__globals__), \
                _paired_stream_hook(root,script,namespace['main'].__globals__), \
                _witness_stream_hook(root,script,namespace['main'].__globals__):
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
