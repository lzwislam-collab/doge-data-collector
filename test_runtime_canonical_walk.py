"""Iterative eligibility differential against the exact previous recursive body."""
import importlib.util
from pathlib import Path
import random
import sys
import unittest


def recursive_oracle(value):
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


class StrictWalk(unittest.TestCase):
    def setUp(self):
        path = Path(__file__).with_name('runtime_audit_cli.py')
        spec = importlib.util.spec_from_file_location('walk_subject', path)
        self.subject = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.subject)

    def same(self, value):
        try:
            expected = recursive_oracle(value)
        except Exception as error:
            with self.assertRaises(type(error)) as got:
                self.subject._json_closed(value)
            self.assertEqual(str(got.exception), str(error))
        else:
            self.assertIs(self.subject._json_closed(value), expected)

    def test_real_graph_requires_no_per_leaf_python_recursion(self):
        value = {'rows': [{'x': i, 'values': [1, 2.0, '汉字', False]} for i in range(1000)]}
        calls, previous = [], sys.getprofile()
        filename = self.subject.__file__
        def observe(frame, event, arg):
            if event == 'call' and frame.f_code.co_filename == filename:
                calls.append(frame.f_code.co_name)
        try:
            sys.setprofile(observe)
            actual = self.subject._json_closed(value)
        finally:
            sys.setprofile(previous)
        self.assertIs(actual, recursive_oracle(value))
        self.assertLessEqual(len(calls), 4, 'per-leaf Python frame overhead remains')

    def test_one_thousand_seeded_builtin_graphs_match_original_exactly(self):
        rng = random.Random(20261110)
        atoms = [None, True, False, 0, 1, -4, 2**150, -0.0, 1e-200, 1e200,
                 float('nan'), float('inf'), '汉字', '\ud800']
        def graph(depth=0):
            if depth > 5 or rng.randrange(3) == 0:
                return rng.choice(atoms)
            kind = rng.randrange(3)
            if kind == 0:
                return [graph(depth+1) for _ in range(rng.randrange(5))]
            if kind == 1:
                return tuple(graph(depth+1) for _ in range(rng.randrange(4)))
            return {str(i): graph(depth+1) for i in range(rng.randrange(5))}
        for index in range(1000):
            with self.subTest(index=index):
                self.same(graph())

    def test_depth_one_hundred_boundary_is_unchanged(self):
        for depth in (0, 1, 98, 99, 100, 101, 102):
            value = 0
            for _ in range(depth):
                value = [value]
            with self.subTest(depth=depth):
                self.same(value)

    def test_one_million_total_visits_boundary_is_unchanged(self):
        for children in (999998, 999999, 1000000):
            with self.subTest(children=children):
                value = [0] * children
                self.same(value)

    def test_nested_cycles_aliases_and_dictionary_cycles_match(self):
        direct = []; direct.append(direct)
        dictionary = {}; dictionary['loop'] = dictionary
        indirect = []; other = [indirect]; indirect.append(other)
        shared = {'x': [1, 2.5]}
        for value in (direct, dictionary, indirect, [shared, shared],
                      {'a': shared, 'b': shared}, [[[]], [], [()]]):
            self.same(value)

    def test_alias_revisits_still_consume_the_original_visit_budget(self):
        for size in (499998, 499999):
            shared = [0] * size
            with self.subTest(size=size):
                self.same([shared, shared])

    def test_exact_key_types_and_builtin_subclasses_match_without_iteration(self):
        touched = []
        class Unknown:
            def __iter__(self):
                touched.append('iterated')
                raise RuntimeError('unknown object must not be iterated')
        class ListChild(list):
            def __iter__(self):
                touched.append('listchild')
                raise RuntimeError('list subclass must not be iterated')
        class DictChild(dict):
            def items(self):
                touched.append('dictchild')
                raise RuntimeError('dict subclass must not be iterated')
        class StrChild(str):
            pass
        for value in (Unknown(), [Unknown()], {Unknown(): 1}, ListChild([1]),
                      DictChild(x=1), StrChild('x'), {StrChild('x'): 1},
                      {None: 1}, {False: 1}, {2.5: 1}, {(1,): 1}):
            self.same(value)
        self.assertEqual(touched, [])

    def test_unsupported_types_nonfinite_atoms_and_custom_metaclass_equalities(self):
        import enum
        class Number(enum.IntEnum):
            ONE = 1
        class EqualMeta(type):
            def __eq__(cls, other):
                return other is int
        class Pretend(metaclass=EqualMeta):
            pass
        for value in (b'x', bytearray(b'x'), memoryview(b'x'), {1}, frozenset({1}),
                      1+2j, Number.ONE, compile('1', 'x', 'eval'), object(),
                      float('nan'), float('inf'), Pretend(), {Pretend(): 1}):
            self.same(value)


if __name__ == '__main__':
    unittest.main()
