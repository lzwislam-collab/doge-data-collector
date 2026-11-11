"""Fresh bounded Git text reads for the unchanged FWD/P0 witness validators.

No raw-byte LRU, witness, row, verdict or error cache. Every eligible original
show is a real sequential request using the existing owned paired transport.
"""
from contextlib import contextmanager
import hashlib
import math
from pathlib import Path
import re
import subprocess
import sys
import threading
import types
import runtime_paired_git_stream as transport
import runtime_process

IMPLEMENTATION = 'witness_fresh_git_blob_stream_v1'
_BLOBS = {'forward_validation.py': 'f0831c7d38581543f84a85639c6d69a7305ff382',
          'forward_validation_v2.py': '3adbdcc43c5dbdb57ecd482b1d4138e720146ba1',
          'p0_operational_audit.py': 'c615bdc665d24481569f545922d3d7aa04594d60'}
_SPEC = re.compile(r'[0-9a-f]{40}:(?:data/direction_v42/forward_registry\.csv|data/stage5/forward_validation_protocol\.json)\Z')


def _compiled(path):
    raw = transport._raw(path)
    if hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest() != _BLOBS[path.name]:
        raise transport._Unavailable()
    return compile(raw, str(path), 'exec', dont_inherit=True)


def _stdlib_original():
    """Same stdlib process boundary checks as the mature paired transport."""
    path = Path(subprocess.__file__)
    code = compile(transport._raw(path), str(path), 'exec', dont_inherit=True)
    runs = [child for child in code.co_consts if isinstance(child, types.CodeType) and child.co_name == 'run']
    run = subprocess.run
    if (len(runs) != 1 or not isinstance(run, types.FunctionType) or run.__code__ != runs[0]
            or run.__globals__ is not subprocess.__dict__ or run.__defaults__ is not None
            or not transport._literal_defaults(run.__kwdefaults__, {'input': None, 'capture_output': False,
                'timeout': None, 'check': False}) or run.__closure__ is not None):
        return False
    classes = [child for child in code.co_consts if isinstance(child, types.CodeType) and child.co_name == 'Popen']
    popen = subprocess.Popen
    if (len(classes) != 1 or type(popen) is not type or popen.__module__ != 'subprocess'
            or popen.__name__ != 'Popen' or popen.__bases__ != (object,) or '__new__' in vars(popen)):
        return False
    methods = [child for child in classes[0].co_consts if isinstance(child, types.CodeType)]
    for name, function in vars(popen).items():
        if name == 'universal_newlines':
            if type(function) is not property or function.fdel is not None: return False
            for accessor in (function.fget, function.fset):
                expected = [child for child in methods if child.co_name == name and
                            isinstance(accessor, types.FunctionType) and accessor.__code__ == child]
                if len(expected) != 1 or not transport._function(accessor, expected[0], subprocess.__dict__): return False
            continue
        if name in {child.co_name for child in methods} and not isinstance(function, types.FunctionType): return False
        if not isinstance(function, types.FunctionType): continue
        if name == '_on_error_fd_closer':
            wrapped = getattr(function, '__wrapped__', None)
            helpers = [child for child in contextmanager.__code__.co_consts
                       if isinstance(child, types.CodeType) and child.co_name == 'helper']
            if (len(helpers) != 1 or function.__code__ != helpers[0]
                    or function.__globals__ is not contextmanager.__globals__
                    or function.__closure__ is None or len(function.__closure__) != 1
                    or function.__closure__[0].cell_contents is not wrapped): return False
            function = wrapped
        if (not isinstance(function, types.FunctionType) or function.__globals__ is not subprocess.__dict__
                or function.__code__.co_name != ('terminate' if sys.platform == 'win32' and name == 'kill' else name)
                or sum(function.__code__ == child for child in methods) != 1): return False
    defaults = {'__init__': (-1,None,None,None,None,None,True,False,None,None,None,None,0,True,False,()),
                'communicate': (None,None), 'wait': (None,), 'poll': None, 'kill': None, 'terminate': None,
                '__enter__': None, '__exit__': None}
    for name, expected in defaults.items():
        function = vars(popen).get(name)
        if not isinstance(function, types.FunctionType) or not transport._literal_defaults(function.__defaults__, expected): return False
        keywords = ({'user':None,'group':None,'extra_groups':None,'encoding':None,'errors':None,
                     'text':None,'umask':-1,'pipesize':-1,'process_group':None} if name == '__init__' else None)
        if not transport._literal_defaults(function.__kwdefaults__, keywords): return False
    return True


def _eligible(root, namespace):
    try:
        if type(namespace) is not dict: return None
        path = Path(namespace['__file__']).resolve()
        if path.parent != root or path.name not in ('forward_validation_v2.py', 'p0_operational_audit.py'): return None
        code = _compiled(path)
        mains = [child for child in code.co_consts if isinstance(child, types.CodeType) and child.co_name == 'main']
        if len(mains) != 1 or not transport._function(namespace.get('main'), mains[0], namespace): return None
        base = namespace['base']
        if type(base) is not types.ModuleType or Path(base.__file__).resolve() != root/'forward_validation.py': return None
        base_code = _compiled(root/'forward_validation.py')
        for name in ('git', 'remote_witnesses'):
            children = [child for child in base_code.co_consts if isinstance(child, types.CodeType) and child.co_name == name]
            defaults = (False,) if name == 'remote_witnesses' else None
            if len(children) != 1 or not transport._function(getattr(base, name, None), children[0], base.__dict__, defaults): return None
        if (base.__dict__.get('subprocess') is not subprocess
                or base.__dict__.get('REG') != 'data/direction_v42/forward_registry.csv'
                or base.__dict__.get('PROTOCOL') != 'data/stage5/forward_validation_protocol.json'
                or not _stdlib_original()): return None
        return base, base.git
    except (AttributeError, KeyError, OSError, TypeError, ValueError, SyntaxError, transport._Unavailable):
        return None


@contextmanager
def installed(root, main_globals, *, trusted_object_directory=None,
              request_timeout=transport.REQUEST_TIMEOUT_SECONDS):
    report = dict(installed=False, implementation=IMPLEMENTATION, requests=0, processes=0,
                  verified_reads=0, fallback_calls=0, reaped=True, reader_joined=True, disabled=False)
    if type(request_timeout) not in (int,float) or not math.isfinite(request_timeout) or request_timeout <= 0:
        raise ValueError('finite positive transport timeout required')
    root = Path(root).resolve()
    eligible = _eligible(root, main_globals)
    if eligible is None:
        yield report; return
    with runtime_process.cancellation_signals():
        with _owned(root, eligible, report, trusted_object_directory, request_timeout) as active:
            yield active


@contextmanager
def _owned(root, eligible, report, trusted_object_directory, request_timeout):
    base, original = eligible
    try: view = transport._View(root, trusted_object_directory)
    except Exception:
        yield report; return
    owner = threading.get_ident(); batch = None; audit_root = root

    def disable():
        nonlocal batch
        report['disabled'] = True
        if batch is not None:
            owned = batch; batch = None; owned.close()

    def git(root, *args):
        nonlocal batch
        call_root = root
        route = len(args) == 2 and args[0] == 'show' and type(args[1]) is str and _SPEC.fullmatch(args[1])
        try: same_root = Path(call_root).resolve() == audit_root
        except (OSError, ValueError, TypeError): same_root = False
        if not route or not same_root or threading.get_ident() != owner: return original(call_root, *args)
        if not report['disabled']:
            try:
                view.check()
                if batch is None:
                    with runtime_process.defer_cancellation():
                        batch = transport._Batch(audit_root, report); report['reaped'] = False; report['reader_joined'] = False
                raw = batch.read(args[1], request_timeout)
                view.check()
                # subprocess.run(text=True,encoding='utf-8') uses strict decoding,
                # universal newlines, then the pinned git helper calls .strip().
                text = raw.decode('utf-8').replace('\r\n', '\n').replace('\r', '\n').strip()
                report['verified_reads'] += 1
                return text
            except Exception:
                disable()
        report['fallback_calls'] += 1
        return original(call_root, *args)

    base.git = git; report['installed'] = True
    try:
        yield report
    finally:
        active_error = sys.exc_info()[1]
        base.git = original
        if batch is not None:
            owned = batch; batch = None
            try: owned.close()
            except BaseException as cleanup_error:
                if active_error is None: raise
                active_error.process_cleanup_error_type = type(cleanup_error).__name__
