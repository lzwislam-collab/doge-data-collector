"""Offline transport equivalence; never executes a science/model CLI."""
import enum
import hashlib
import importlib.util
import json
import marshal
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


def load():
    path = Path(__file__).with_name('runtime_audit_cli.py')
    spec = importlib.util.spec_from_file_location('snapshot_subject', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SnapshotCertificateTests(unittest.TestCase):
    def setUp(self):
        self.subject = load()
        self.encoder = json.dumps
        self.original = lambda value: hashlib.sha256(self.encoder(value, sort_keys=True,
            separators=(',', ':'), allow_nan=False).encode()).hexdigest()

    def cache(self, **kwargs):
        return self.subject._CanonicalBytes(self.original, **kwargs)

    def equivalent(self, cache, value):
        try:
            expected = self.original(value)
        except Exception as error:
            with self.assertRaises(type(error)) as got:
                cache.digest(value)
            self.assertEqual(str(got.exception), str(error))
        else:
            self.assertEqual(cache.digest(value), expected)

    def test_closed_values_need_no_python_walk_and_rehash_every_hit(self):
        value = {'x': [{'name': '汉字', 'value': 1.25}, None, False], 'z': -0.0}
        cache = self.cache()
        sha = hashlib.sha256
        expected = self.original(value)
        # Observe actual standard encoder entries without replacing its identity.
        encoded, previous = [], sys.getprofile()
        def observe(frame, event, arg):
            if event == 'call' and frame.f_code is self.encoder.__code__:
                encoded.append(1)
        try:
            sys.setprofile(observe)
            with patch.object(self.subject, '_json_closed', side_effect=AssertionError('walk forbidden')), \
                 patch.object(self.subject.hashlib, 'sha256', wraps=sha) as hashed:
                for _ in range(3):
                    self.assertEqual(cache.digest(value), expected)
        finally:
            sys.setprofile(previous)
        self.assertEqual(hashed.call_count, 3)
        self.assertEqual(len(encoded), 1)
        self.assertEqual(cache.stats['fallback_calls'], 0)
        self.assertEqual(cache.stats['encoded_calls'], 1)
        self.assertEqual(cache.stats['hits'], 2)

    def test_root_numpy_buffer_and_bytes_collision_never_populates_cache(self):
        import numpy as np
        value = np.float64(1.25)
        raw = memoryview(value).tobytes()
        self.assertEqual(marshal.dumps(value, 2), marshal.dumps(raw, 2))
        cache = self.cache()
        for item in (value, raw, value, raw):
            self.equivalent(cache, item)
        self.assertEqual(cache.stats['entries'], 0)
        self.assertEqual(cache.stats['hits'], 0)

    def test_nested_numpy_buffer_and_bytes_collision_never_hits(self):
        import numpy as np
        value = np.float64(-0.0)
        raw = memoryview(value).tobytes()
        values = ({'x': [value]}, {'x': [raw]})
        self.assertEqual(*(marshal.dumps(item, 2) for item in values))
        cache = self.cache()
        for item in values * 2:
            self.equivalent(cache, item)
        self.assertEqual(cache.stats['entries'], 0)

    def test_subclasses_and_custom_iterators_keep_original_behavior(self):
        class Number(enum.IntEnum):
            ONE = 1
        class DictChild(dict):
            def items(self):
                return [('visible', [7])]
        class ListChild(list):
            def __iter__(self):
                return iter([9])
        class StringChild(str):
            pass
        cache = self.cache()
        for value in (Number.ONE, DictChild(hidden=2), ListChild([1]), StringChild('x')):
            self.equivalent(cache, value)
        self.assertEqual(cache.stats['entries'], 0)

    def test_nonfinite_cycles_unknown_and_mixed_keys_preserve_exact_exception(self):
        cycle = []
        cycle.append(cycle)
        cache = self.cache()
        for value in (float('nan'), float('inf'), -float('inf'), {'x': float('nan')},
                      {'a': 1, 2: 3}, cycle, b'x', bytearray(b'x'), memoryview(b'x'),
                      {1}, frozenset({1}), 1+2j, compile('1', 'x', 'eval'), object()):
            for _ in range(2):
                self.equivalent(cache, value)
        self.assertEqual(cache.stats['entries'], 0)

    def test_negative_zero_large_int_unicode_and_numeric_keys(self):
        cache = self.cache()
        for value in (-0.0, 0.0, 2**180, '\ud800汉字', {1: 'a', 2.5: 'b'},
                      {None: 'a'}, {False: 2, True: 3}, (1, 2.5), {'z': -0.0}):
            for _ in range(3):
                self.equivalent(cache, value)
        self.assertNotEqual(cache.digest(-0.0), cache.digest(0.0))

    def test_alias_expansion_and_local_reference_change_preserve_content_key(self):
        cache = self.cache()
        shared = {'x': [1, 2.5]}
        value = {'a': shared, 'b': shared}
        expanded = {'a': {'x': [1, 2.5]}, 'b': {'x': [1, 2.5]}}
        for item in (value, expanded, value):
            candidate = item['a']
            self.equivalent(cache, item)
            del candidate
        self.assertEqual(cache.stats['encoded_calls'], 1)
        self.assertEqual(cache.stats['hits'], 2)

    def test_snapshot_mutation_after_encode_uses_full_original_and_never_stores(self):
        value = {'x': 1}
        encoder = self.encoder
        def mutated(item, **kwargs):
            encoded = encoder(item, **kwargs)
            item['x'] = 2
            return encoded
        # The real original digest sees the same global encoder as the wrapper.
        original = lambda item: hashlib.sha256(json.dumps(item, sort_keys=True,
            separators=(',', ':'), allow_nan=False).encode()).hexdigest()
        cache = self.subject._CanonicalBytes(original)
        with patch.object(self.subject.json, 'dumps', side_effect=mutated):
            self.assertEqual(cache.digest(value), self.original({'x': 1}))
        self.assertEqual(value, {'x': 2})
        self.assertEqual(cache.stats['entries'], 0)

    def test_mutation_before_encode_uses_full_original_and_no_old_key(self):
        value = {'x': 1}
        encoder = self.encoder
        def mutated(item, **kwargs):
            item['x'] = 2
            return encoder(item, **kwargs)
        original = lambda item: hashlib.sha256(json.dumps(item, sort_keys=True,
            separators=(',', ':'), allow_nan=False).encode()).hexdigest()
        cache = self.subject._CanonicalBytes(original)
        with patch.object(self.subject.json, 'dumps', side_effect=mutated):
            self.assertEqual(cache.digest(value), self.original({'x': 2}))
        self.assertEqual(value, {'x': 2})
        self.assertEqual(cache.stats['entries'], 0)

    def test_marshal_decode_or_encode_error_falls_back_with_original_return(self):
        for name in ('dumps', 'loads'):
            cache = self.cache()
            with patch.object(self.subject.marshal, name, side_effect=ValueError('internal')):
                self.assertEqual(cache.digest({'x': 1}), self.original({'x': 1}))
            self.assertEqual(cache.stats['entries'], 0)

    def test_replaced_encoder_never_hits_existing_cache_or_changes_hook_effects(self):
        seen = []
        original = lambda item: hashlib.sha256(json.dumps(item, sort_keys=True,
            separators=(',', ':'), allow_nan=False).encode()).hexdigest()
        cache = self.subject._CanonicalBytes(original)
        value = {'x': 1}
        self.assertEqual(cache.digest(value), self.original(value))
        def hooked(item, **kwargs):
            seen.append(item)
            return self.encoder(item, **kwargs)
        with patch.object(self.subject.json, 'dumps', side_effect=hooked):
            self.assertEqual(cache.digest(value), self.original(value))
        self.assertEqual(seen, [value])
        self.assertIs(seen[0], value)
        self.assertEqual(cache.stats['hits'], 0)
        self.assertEqual(cache.stats['fallback_calls'], 1)

    def test_replaced_encoder_exception_object_is_original_and_not_cached(self):
        error = RuntimeError('original encoder error')
        original = lambda item: hashlib.sha256(json.dumps(item, sort_keys=True,
            separators=(',', ':'), allow_nan=False).encode()).hexdigest()
        cache = self.subject._CanonicalBytes(original)
        with patch.object(self.subject.json, 'dumps', side_effect=error), \
             self.assertRaises(RuntimeError) as got:
            cache.digest({'x': 1})
        self.assertIs(got.exception, error)
        self.assertEqual(cache.stats['entries'], 0)

    def test_encoder_class_replacement_with_dumps_unchanged_keeps_original(self):
        import numpy as np
        original_class = json.JSONEncoder
        class CustomEncoder(original_class):
            def default(self, value):
                if isinstance(value, bytes):
                    return 'BYTES:' + value.hex()
                return super().default(value)
        cache = self.cache()
        function = json.dumps
        with patch.object(json, 'JSONEncoder', CustomEncoder):
            self.equivalent(cache, np.float64(1.25))
        self.assertIs(json.dumps, function)
        self.assertEqual(cache.stats['entries'], 0)
        self.assertEqual(cache.stats['fallback_calls'], 1)

    def test_same_encoder_class_each_method_replacement_uses_complete_original(self):
        import numpy as np
        for method in ('__init__', 'encode', 'iterencode', 'default'):
            with self.subTest(method=method):
                function = getattr(json.JSONEncoder, method)
                cache = self.cache()
                def replaced(*args, **kwargs):
                    return function(*args, **kwargs)
                with patch.object(json.JSONEncoder, method, replaced):
                    self.equivalent(cache, {'x': 1})
                self.assertEqual(cache.stats['entries'], 0)
                self.assertEqual(cache.stats['fallback_calls'], 1)

    def test_encoder_factory_and_ascii_encoder_replacement_never_certify(self):
        for name in ('_make_iterencode', 'c_make_encoder', 'encode_basestring_ascii',
                     'encode_basestring'):
            with self.subTest(name=name):
                function = getattr(json.encoder, name)
                cache = self.cache()
                def replaced(*args, **kwargs):
                    return function(*args, **kwargs)
                with patch.object(json.encoder, name, replaced):
                    self.equivalent(cache, {'x': '汉字'})
                self.assertEqual(cache.stats['entries'], 0)
                self.assertEqual(cache.stats['fallback_calls'], 1)

    def test_default_encoder_identity_and_state_changes_require_original(self):
        for kind in ('identity', 'state'):
            with self.subTest(kind=kind):
                cache = self.cache()
                if kind == 'identity':
                    context = patch.object(json, '_default_encoder', json.JSONEncoder())
                else:
                    context = patch.object(json._default_encoder, 'allow_nan', False)
                with context:
                    self.equivalent(cache, {'x': 1})
                self.assertEqual(cache.stats['entries'], 0)
                self.assertEqual(cache.stats['fallback_calls'], 1)

    def test_same_function_object_changed_code_or_kwdefaults_requires_original(self):
        function = json.dumps
        code, kwdefaults = function.__code__, dict(function.__kwdefaults__)
        def replaced(*args, **kwargs):
            return '"replaced"'
        cache = self.cache()
        try:
            function.__code__ = replaced.__code__
            self.equivalent(cache, {'x': 1})
        finally:
            function.__code__ = code
        self.assertEqual(cache.stats['entries'], 0)
        cache = self.cache()
        try:
            function.__kwdefaults__['ensure_ascii'] = False
            self.equivalent(cache, {'x': '汉字'})
        finally:
            function.__kwdefaults__.clear()
            function.__kwdefaults__.update(kwdefaults)
        self.assertEqual(cache.stats['entries'], 0)

    def test_same_class_method_changed_code_requires_original(self):
        function = json.JSONEncoder.default
        code = function.__code__
        def replaced(self, value):
            return 'accepted bytes'
        import numpy as np
        cache = self.cache()
        try:
            function.__code__ = replaced.__code__
            self.equivalent(cache, np.float64(1.25))
        finally:
            function.__code__ = code
        self.assertEqual(cache.stats['entries'], 0)

    def test_preexisting_foreign_class_method_cannot_be_captured_as_standard(self):
        function = json.JSONEncoder.encode
        def foreign(self, value):
            return function(self, value)
        with patch.object(json.JSONEncoder, 'encode', foreign):
            subject = load()
            cache = subject._CanonicalBytes(self.original)
            self.assertEqual(cache.digest({'x': 1}), self.original({'x': 1}))
        self.assertEqual(cache.stats['entries'], 0)
        self.assertEqual(cache.stats['fallback_calls'], 1)

    def test_real_marshal_audit_callback_chain_changes_never_certify_or_hit(self):
        # Real audit hooks cannot be removed: isolate every boundary in a child.
        source = r'''
import hashlib,importlib.util,json,sys
import numpy as np
spec=importlib.util.spec_from_file_location('audit_subject',SUBJECT_PATH)
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
original=lambda v:hashlib.sha256(json.dumps(v,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
standard=json.JSONEncoder
class Changed(standard):
 def default(self,value):
  if isinstance(value,bytes):return 'BUFFER:'+value.hex()
  return super().default(value)
 def encode(self,value):
  if isinstance(value,dict):return '"HOOKED_DICT"'
  return super().encode(value)
stage=sys.argv[1];count=0;fired=False
cache=m._CanonicalBytes(original)
value={'x':1} if stage in ('post_value','post_snapshot','hit_dump') else np.float64(1.25)
if stage=='hit_dump':cache.digest(value)
def audit(event,args):
 global count,fired
 if event=='marshal.dumps':count+=1
 trigger=((stage in ('first_dump','hit_dump') and event=='marshal.dumps' and count==1)
          or (stage=='load' and event=='marshal.loads')
          or (stage=='post_value' and event=='marshal.dumps' and count==2)
          or (stage=='post_snapshot' and event=='marshal.dumps' and count==3))
 if trigger and not fired:
  fired=True;json.JSONEncoder=Changed
sys.addaudithook(audit)
actual=cache.digest(value);expected=original(value)
report={'same':actual==expected,'fired':fired,'cache':cache.stats,'dump_count':count}
print(standard(sort_keys=True).encode(report))
'''.replace('SUBJECT_PATH', repr(str(Path(__file__).with_name('runtime_audit_cli.py'))))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'audit_child.py'
            path.write_text(source, encoding='utf-8')
            for stage in ('first_dump', 'load', 'post_value', 'post_snapshot', 'hit_dump'):
                with self.subTest(stage=stage):
                    done = subprocess.run([sys.executable, '-B', str(path), stage],
                        capture_output=True, text=True, timeout=30, check=True)
                    report = json.loads(done.stdout)
                    self.assertTrue(report['fired'])
                    self.assertTrue(report['same'])
                    self.assertEqual(report['cache']['fallback_calls'], 1)
                    self.assertEqual(report['cache']['hits'], 0)
                    self.assertEqual(report['cache']['entries'], 1 if stage=='hit_dump' else 0)

    def test_original_fallback_exception_after_decode_chain_change_called_once(self):
        from unittest.mock import Mock
        standard_class, decoder = json.JSONEncoder, marshal.loads
        class Changed(standard_class):
            pass
        error = RuntimeError('original business exception')
        original = Mock(side_effect=error)
        cache = self.subject._CanonicalBytes(original)
        def changed(raw):
            json.JSONEncoder = Changed
            return decoder(raw)
        try:
            with patch.object(self.subject.marshal, 'loads', side_effect=changed), \
                 self.assertRaises(RuntimeError) as got:
                cache.digest({'x': 1})
        finally:
            json.JSONEncoder = standard_class
        self.assertIs(got.exception, error)
        self.assertEqual(original.call_count, 1)
        self.assertEqual(cache.stats['fallback_calls'], 1)
        self.assertEqual(cache.stats['entries'], 0)

    def test_current_value_mutation_after_clone_falls_back_without_old_key(self):
        value = {'x': 1}
        decoder = marshal.loads
        def changed(raw):
            snapshot = decoder(raw)
            value['x'] = 2
            return snapshot
        cache = self.cache()
        with patch.object(self.subject.marshal, 'loads', side_effect=changed):
            self.assertEqual(cache.digest(value), self.original({'x': 2}))
        self.assertEqual(cache.stats['entries'], 0)

    def test_snapshot_postcheck_rejects_change_even_if_current_value_unchanged(self):
        value = {'x': 1}
        decoder, encoder = marshal.loads, marshal.dumps
        held, calls = [], []
        def captured(raw):
            snapshot = decoder(raw)
            held.append(snapshot)
            return snapshot
        def changed(item, *args):
            calls.append(item)
            if len(calls) == 2:
                held[0]['x'] = 2
            return encoder(item, *args)
        cache = self.cache()
        with patch.object(self.subject.marshal, 'loads', side_effect=captured), \
             patch.object(self.subject.marshal, 'dumps', side_effect=changed):
            self.assertEqual(cache.digest(value), self.original(value))
        self.assertEqual(value, {'x': 1})
        self.assertEqual(cache.stats['entries'], 0)

    def test_seeded_json_values_and_repeated_nested_mutations(self):
        rng = random.Random(20261110)
        atoms = [None, True, False, 0, -0.0, 1, -8, 2**100, 1e-200, '汉字', '\ud800']
        def value(depth=0):
            if depth > 3 or rng.randrange(3) == 0:
                return rng.choice(atoms)
            if rng.randrange(2):
                return [value(depth+1) for _ in range(rng.randrange(4))]
            return {str(i): value(depth+1) for i in range(rng.randrange(4))}
        cache = self.cache()
        for _ in range(500):
            item = value()
            self.equivalent(cache, item)
            self.equivalent(cache, item)
        mutable = {'x': [{'value': 1}]}
        for replacement in atoms:
            mutable['x'][0]['value'] = replacement
            self.equivalent(cache, mutable)

    def test_large_graph_lru_caps_and_original_limits_unchanged(self):
        cache = self.cache(max_bytes=4096, max_entries=3)
        for size in (5, 20, 1000, 50, 1000):
            self.equivalent(cache, {'x': [{'i': i, 'name': 'é'} for i in range(size)]})
            self.assertLessEqual(cache.stats['cached_bytes'], 4096)
            self.assertLessEqual(cache.stats['entries'], 3)
        self.assertTrue(all(type(k) is bytes and type(v) is bytes
                            for k, v in cache.entries.items()))


if __name__ == '__main__':
    unittest.main()
