"""Dictionary union slots and in-place mapping updates against CPython."""

from pathlib import Path

from .conftest import assert_matches, run_cpython, run_diaspore_lua

_SOURCE = r'''
left = {'a': 1, 'b': 2}
right = {'b': 20, 'c': 30}
print(left | right, left, right)
original = left
left |= [('c', 3), ('a', 4)]
print(left, left is original)
left |= left
print(left)

class Mapping:
    def keys(self):
        print('keys')
        return ['b', 'd']

    def __getitem__(self, key):
        print('get', key)
        return key.upper()

    def __ror__(self, other):
        print('reflected mapping')
        return 'reflected'

left |= Mapping()
print(left, left is original)
print({} | Mapping())

class Child(dict):
    pass

child = Child(a=1)
print(type(child | {'b': 2}).__name__, type({'b': 2} | child).__name__)
original = child
child |= [('c', 3)]
print(type(child).__name__, child is original, child)

class KeysOnly(Child):
    def keys(self):
        print('subclass keys')
        return ['virtual']

    def __getitem__(self, key):
        print('subclass get', key)
        return 9

class CustomIterator(KeysOnly):
    def __iter__(self):
        return iter(self.keys())

for source in [KeysOnly(stored=1), CustomIterator(stored=1)]:
    print({} | source, source | {})
    target = {}
    target |= source
    print(target)

class Reflected(Child):
    def __ror__(self, other):
        return ('reflected child', list(other))

print({'first': 1} | Reflected())
print(Child(first=1) | Reflected())

class Custom(Child):
    def __or__(self, other):
        return ('custom or', list(other))

    def __ior__(self, other):
        print('custom ior')
        return self

custom = Custom()
print(custom | {'z': 0})
original = custom
custom |= [('x', 1)]
print(custom is original, custom)

class Fallback(Child):
    def __ior__(self, other):
        return NotImplemented

fallback = Fallback(a=1)
original = fallback
fallback |= {'b': 2}
print(type(fallback).__name__, fallback is original, fallback, original)

for invalid in [1, [('x', 1)], Mapping()]:
    try:
        print(dict.__or__({}, invalid) is NotImplemented)
    except TypeError as error:
        print(type(error).__name__)
print(dict.__ror__({'a': 1}, {'b': 2}))
child = Child()
print(dict.__ior__(child, [('x', 1)]) is child, child)

for invalid in [1, [('first', 1), ('bad',)], [('first', 1), 0]]:
    current = {}
    try:
        current |= invalid
    except (TypeError, ValueError) as error:
        print(type(error).__name__, str(error), current)

def pairs():
    yield ('a', 1)
    yield ('b', 2)

current = {}
current |= pairs()
print(current)
'''


def test_lua_dict_union(tmp_path: Path, lua_vm: list[str]) -> None:
    entry = tmp_path / "dict_union.py"
    entry.write_text(_SOURCE, encoding="utf-8")
    expected = run_cpython(entry)
    assert expected.code == 0, expected.err_line
    assert_matches(run_diaspore_lua(entry, lua_vm, tmp_path), expected, "Lua dict union")
