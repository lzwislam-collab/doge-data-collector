"""Use the existing complete native history reader in the original rescue.

Only two call sites are temporarily routed. Every invocation rechecks reviewed
source/function/alias identities; unknown identities execute the saved original.
No result cache, model, new permission, request, scheduler, or window change.
"""
import ast
from contextlib import contextmanager
import hashlib
import importlib
import json
from pathlib import Path
import sys
import types

IMPLEMENTATION = 'quote_rescue_original_native_history_v1'
ROOT = Path(__file__).resolve().parent
PINS = {
    'direction_baseline_v42.py': '4bd0417548ab8251ec9d92e168db46d6ec1068bb',
    'direction_forward_v42.py': '368abc641db6d5ea6496ac4db624554161034665',
    'stage5_70pct_audit.py': '1095886e5e493676e15a2de5c2bf0bd1abbd7dc9',
    'stage5_forward_quality.py': '8c9717dd2fabb49ed3ba1c3f4c1df1281d13f51b',
    'forward_validation.py': 'f0831c7d38581543f84a85639c6d69a7305ff382',
    'forward_validation_v2.py': '3adbdcc43c5dbdb57ecd482b1d4138e720146ba1',
    'spot_plus_quote_watch.py': '132ac443c8421960b19c43e237b9e638609c5b23',
    'quote_evidence_v2.py': '7d1e62e60f3aa78949ef1d5d81d74086d1907c91',
    'quote_capture_retry.py': '0cc8f737040c704ca123b36eb4ffa556bc316046',
    'publish_forward_evidence.py': 'd982f4f8bd278d7a1505073bc35dd71b80eeb095',
    'quote_only_rescue.py': '3237bd502c411c54bf79969e3923fd5e32f25b4c',
    'runtime_publication_state.py': 'f0f6b6c7dbe5d7e4c0995b4714a53957217fbac0',
    'data/stage5/forward_validation_protocol.json': '541d33221fde851354ced0281b7fd0c4be715da5',
    'data/stage5/forward_validation_protocol_v2.json': '79478022e4c33da8f3e08af8e049e668d06cdb7b',
}
MODULES = {Path(name).stem: importlib.import_module(Path(name).stem)
           for name in PINS if name.endswith('.py') and name != 'runtime_publication_state.py'}
rescue = MODULES['quote_only_rescue']
publisher = MODULES['publish_forward_evidence']
evidence = MODULES['quote_evidence_v2']
base = MODULES['forward_validation']
native = None
_UNKNOWN = object()


def _backend():
    """Unknown backend bytes must never execute, even during initial import."""
    global native
    name = 'runtime_publication_state'
    if native is not None and native is not MODULES.get(name):
        return None
    try:
        path = ROOT / (name + '.py')
        if path.is_symlink():
            return None
        raw = path.read_bytes()
        oid = hashlib.sha1(b'blob ' + str(len(raw)).encode('ascii') + b'\0' + raw).hexdigest()
        if oid != PINS[path.name]:
            return None
        loaded = importlib.import_module(name)
        if native is not None and loaded is not native:
            return None
    except Exception:
        # Only trusted-module availability/import is attempted here. No
        # validator or business function is inside this compatibility catch.
        return None
    if name not in MODULES:
        MODULES[name] = loaded
    if loaded is not MODULES[name]:
        return None
    native = loaded
    return native


def _literal(node, root):
    if isinstance(node, ast.Name) and node.id == 'ROOT':
        return root
    try:
        return ast.literal_eval(node)
    except (ValueError, TypeError):
        return _UNKNOWN


def _function(value, node, compiled, namespace, root):
    matches = [child for child in compiled.co_consts
               if isinstance(child, types.CodeType) and child.co_name == node.name]
    defaults = tuple(_literal(item, root) for item in node.args.defaults) or None
    keywords = {arg.arg: _literal(item, root) for arg, item in
                zip(node.args.kwonlyargs, node.args.kw_defaults) if item is not None} or None
    return (type(value) is types.FunctionType and len(matches) == 1
            and value.__code__ == matches[0] and value.__globals__ is namespace
            and Path(value.__code__.co_filename).resolve() == Path(namespace['__file__']).resolve()
            and value.__name__ == node.name and value.__module__ == namespace['__name__']
            and value.__closure__ is None and value.__defaults__ == defaults
            and value.__kwdefaults__ == keywords)


def _eligible(root, original_publisher, original_alias, validate_hook, history_hook):
    """Optimization eligibility only: never run a business check here."""
    def unhook(module, name, value):
        if module is publisher and name == 'validate_state' and value is validate_hook:
            return original_publisher
        if module is evidence and name == 'history_receipts' and value is history_hook:
            return original_alias
        return value
    try:
        if any(actual is not MODULES[name] for name, actual in (
                ('quote_only_rescue', rescue), ('publish_forward_evidence', publisher),
                ('quote_evidence_v2', evidence), ('forward_validation', base),
                ('runtime_publication_state', native))):
            return 'adapter_module_alias_changed'
        if Path(root).resolve() != ROOT:
            return 'call_root_changed'
        for relative, expected in PINS.items():
            path = ROOT / relative
            if path.is_symlink():
                return 'source_path_changed'
            raw = path.read_bytes()
            oid = hashlib.sha1(b'blob ' + str(len(raw)).encode('ascii') + b'\0' + raw).hexdigest()
            if oid != expected:
                return 'source_identity_changed'
            if not relative.endswith('.py'):
                continue
            name = Path(relative).stem
            module = MODULES[name]
            if (type(module) is not types.ModuleType or sys.modules.get(name) is not module
                    or Path(module.__file__).resolve() != path):
                return 'module_identity_changed'
            namespace = module.__dict__
            if 'ROOT' in namespace and namespace['ROOT'] != ROOT:
                return 'module_root_changed'
            compiled = compile(raw, str(path), 'exec', dont_inherit=True)
            for node in ast.parse(raw).body:
                if isinstance(node, ast.FunctionDef):
                    value = unhook(module, node.name, namespace.get(node.name))
                    if not _function(value, node, compiled, namespace, ROOT):
                        return 'function_identity_changed'
                elif isinstance(node, ast.Import):
                    for item in node.names:
                        if item.name in MODULES and namespace.get(item.asname or item.name) is not MODULES[item.name]:
                            return 'module_alias_changed'
                elif isinstance(node, ast.ImportFrom) and node.module in MODULES:
                    source = MODULES[node.module]
                    for item in node.names:
                        actual = unhook(module, item.asname or item.name,
                                        namespace.get(item.asname or item.name))
                        wanted = unhook(source, item.name, getattr(source, item.name))
                        if actual is not wanted:
                            return 'early_alias_changed'
                elif isinstance(node, ast.Assign):
                    wanted = _literal(node.value, ROOT)
                    if wanted is not _UNKNOWN:
                        for target in node.targets:
                            if isinstance(target, ast.Name) and namespace.get(target.id, _UNKNOWN) != wanted:
                                return 'module_constant_changed'
        compatibility = native._NativeCompatibility
        if (type(compatibility) is not type or compatibility.__bases__ != (Exception,)
                or compatibility.__name__ != '_NativeCompatibility'
                or compatibility.__module__ != native.__name__):
            return 'backend_exception_identity_changed'
        if (type(native._OID.pattern) is not str
                or native._OID.pattern != r'(?:[0-9a-f]{40}|[0-9a-f]{64})\Z'):
            return 'backend_pattern_changed'
        return None
    except (OSError, ValueError, TypeError, AttributeError, KeyError, SyntaxError):
        return 'identity_unavailable'


def _record(report, kind, reason, diagnostics):
    # Reporting is bounded, stderr-only, and cannot replace the real outcome.
    try:
        fields = ('implementation', 'mode', 'status', 'attempted_commit_count',
                  'native_batch_calls', 'fallback_reason', 'relative')
        short = {key: diagnostics[key] for key in fields if key in diagnostics}
        if 'histories' in diagnostics:
            short['histories'] = [{key: item[key] for key in fields if key in item}
                                  for item in diagnostics['histories'][:3]]
        if reason is not None:
            mode = 'legacy'
        else:
            attempt_key = 'native_' + kind + '_attempts'
            report[attempt_key] = report.get(attempt_key, 0) + 1
            if diagnostics.get('mode') == 'legacy_full_fallback':
                mode = 'fallback'
            elif diagnostics.get('mode') == 'native_batch' and diagnostics.get('status') == 'ok':
                mode = 'native'
            else:
                mode = 'native_error'
        call = {'kind': kind, 'mode': mode,
                'reason': reason, 'diagnostics': short}
        key = mode + '_' + kind + '_calls'
        report[key] = report.get(key, 0) + 1
        if len(report['calls']) < 32:
            report['calls'].append(call)
        line = json.dumps(dict(call, implementation=IMPLEMENTATION), sort_keys=True, allow_nan=False)
        if len(line) <= 4096:
            print(line, file=sys.stderr, flush=True)
    except BaseException:
        pass


@contextmanager
def installed(root=ROOT):
    """Temporarily route the two existing call sites; restore even on signals."""
    original_publisher = publisher.validate_state
    original_alias = evidence.history_receipts
    original_history = base.history_receipts
    report = {'implementation': IMPLEMENTATION, 'calls': [],
              'native_validation_calls': 0, 'native_history_calls': 0,
              'native_validation_attempts': 0, 'native_history_attempts': 0,
              'legacy_validation_calls': 0, 'legacy_history_calls': 0,
              'fallback_validation_calls': 0, 'fallback_history_calls': 0}
    if _backend() is None:
        _record(report, 'installation', 'backend_unavailable', {})
        yield report
        return

    def validate_state(root):
        previous = publisher.validate_state
        # The unchanged backend can fall back to publisher.validate_state.
        # Restore that original before every attempt, preventing recursion.
        publisher.validate_state = original_publisher
        diagnostics = {}
        reason = 'identity_unavailable'
        try:
            reason = _eligible(root, original_publisher, original_alias, validate_state, history_receipts)
            if reason:
                return original_publisher(root)
            return native.validate_state(root, diagnostics)
        finally:
            try:
                publisher.validate_state = previous
            finally:
                _record(report, 'validation', reason, diagnostics)

    def history_receipts(root, relative, columns, current):
        diagnostics = {}
        reason = 'identity_unavailable'
        try:
            reason = _eligible(root, original_publisher, original_alias, validate_state, history_receipts)
            if reason:
                return original_alias(root, relative, columns, current)
            return native.history_receipts(root, relative, columns, current, diagnostics)
        finally:
            _record(report, 'history', reason, diagnostics)

    try:
        publisher.validate_state = validate_state
        if original_alias is original_history:
            evidence.history_receipts = history_receipts
        yield report
    finally:
        try:
            publisher.validate_state = original_publisher
        finally:
            evidence.history_receipts = original_alias


def main():
    with installed():
        return rescue.main()


if __name__ == '__main__':
    raise SystemExit(main())
