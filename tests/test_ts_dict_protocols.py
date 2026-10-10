"""CPython dictionary update protocol dispatch and observable partial mutations."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from diaspore import compile_bundle

from .conftest import TS_DIR, Result, assert_matches, run_cpython

_SOURCES = [
    """class Mapping:
    def keys(self):
        print('keys')
        return ['b', 'a', 'b']
    def __getitem__(self, key):
        print('get', key)
        return key.upper()
    def __iter__(self):
        raise AssertionError('mapping must not iterate source')
for mode in ['constructor', 'update', 'ior', 'augmented']:
    print(mode)
    source = Mapping()
    target = {'seed': 0}
    if mode == 'constructor':
        target = dict(source, end=9)
    elif mode == 'update':
        print(target.update(source, end=9))
    elif mode == 'ior':
        print(target.__ior__(source) is target)
    else:
        original = target
        target |= source
        print(target is original)
    print(list(target.items()))
class LiveKeys:
    def __init__(self):
        self.names = ['a']
    def keys(self):
        return self.names
    def __getitem__(self, key):
        if key == 'a':
            self.names.append('b')
        return key
print(dict(LiveKeys()))
class MissingKeys:
    @property
    def keys(self):
        raise AttributeError('not a mapping')
    def __iter__(self):
        yield ('pair', 4)
print(dict(MissingKeys()))
""",
    """class Problem(Exception):
    pass
class Pair:
    def __iter__(self):
        print('pair first')
        yield 'second'
        print('pair second')
        yield 2
        print('pair done')
def source(target):
    print('outer first', list(target.items()))
    yield ('first', 1)
    print('outer second', list(target.items()))
    yield Pair()
    print('outer failure', list(target.items()))
    raise Problem('outer stopped')
for mode in ['update', 'ior', 'augmented']:
    target = {}
    print(mode)
    try:
        if mode == 'update':
            target.update(source(target), final=9)
        elif mode == 'ior':
            target.__ior__(source(target))
        else:
            target |= source(target)
    except Problem as error:
        print(type(error).__name__, str(error), list(target.items()))
for bad in [None, 1, [], ['one'], [1, 2, 3]]:
    target = {}
    try:
        target.update([('ok', 1), bad], final=9)
    except (TypeError, ValueError) as error:
        print(type(error).__name__, str(error), list(target.items()))
class PairTypeError:
    def __iter__(self):
        yield 'key'
        raise TypeError('inner type error')
class PairCustomError:
    def __iter__(self):
        yield 'key'
        raise Problem('inner custom error')
for pair in [PairTypeError(), PairCustomError()]:
    target = {}
    try:
        target.update([('ok', 1), pair])
    except Exception as error:
        print(type(error).__name__, str(error), list(target.items()))
""",
    """class Problem(Exception):
    pass
class KeysFail:
    def keys(self):
        print('keys start')
        yield 'a'
        print('keys failure')
        raise Problem('keys stopped')
    def __getitem__(self, key):
        print('unexpected item access', key)
        return 1
class ItemFail:
    def keys(self):
        print('keys all')
        yield 'a'
        yield 'b'
        yield 'c'
        print('keys done')
    def __getitem__(self, key):
        print('get', key)
        if key == 'b':
            raise Problem('item stopped')
        return 1
class PropertyFail:
    @property
    def keys(self):
        raise Problem('property stopped')
class NotCallable:
    keys = None
for source in [KeysFail(), ItemFail(), PropertyFail(), NotCallable()]:
    target = {'seed': 0}
    try:
        target.update(source, final=9)
    except Exception as error:
        print(type(error).__name__, str(error), list(target.items()))
""",
    """class Direct(dict):
    def keys(self):
        print('custom keys')
        return ['custom']
    def __getitem__(self, key):
        print('custom get', key)
        return 12
class Iterated(Direct):
    def __iter__(self):
        print('custom iter must not run')
        return iter(['ignored'])
class Restored(Direct):
    __iter__ = dict.__iter__
for cls in [Direct, Iterated, Restored]:
    source = cls(original=3)
    print(cls.__name__, dict(source))
    target = {}
    target.update(source)
    print(target)
    target = {}
    print(target.__ior__(source) is target, target)
class LockedKey:
    locked = False
    def __hash__(self):
        if self.locked:
            raise AssertionError('rehash')
        return 17
    def __eq__(self, other):
        raise AssertionError('unexpected equality')
key = LockedKey()
source = {key: 9}
key.locked = True
target = {}
target.update(source)
print(len(target), next(iter(target)) is key)
print(target.update(target), len(target))
""",
    """class PairSequence:
    def __getitem__(self, index):
        print('pair index', index)
        if index == 0:
            return 'sequence'
        if index == 1:
            return 7
        raise IndexError
class OuterSequence:
    def __getitem__(self, index):
        print('outer index', index)
        if index == 0:
            return PairSequence()
        raise IndexError
print(dict(OuterSequence()))
class Disabled(OuterSequence):
    __iter__ = None
class BadIterator:
    def __iter__(self):
        return [('bad', 1)]
class NextOnly:
    def __next__(self):
        return ('bad', 1)
for source in [Disabled(), BadIterator(), NextOnly()]:
    try:
        dict(source)
    except TypeError as error:
        print(type(error).__name__, str(error))
""",
]


@pytest.mark.parametrize(
    "source",
    _SOURCES,
    ids=[
        "mapping-dispatch",
        "streaming-pairs",
        "mapping-errors",
        "dict-subclass-fastpath",
        "sequence-iteration",
    ],
)
def test_ts_dictionary_update_protocols(tmp_path: Path, source: str) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js not installed")
    entry = tmp_path / "dict_protocol.py"
    entry.write_text(source, encoding="utf-8")
    expected = run_cpython(entry)
    assert expected.code == 0, expected.err_line
    path = tmp_path / "dict_protocol.dsp.json"
    path.write_text(json.dumps(compile_bundle(str(entry), stdlib=False)), encoding="utf-8")
    process = subprocess.run(
        [
            node,
            "--experimental-strip-types",
            "--no-warnings",
            str(TS_DIR / "src/cli.ts"),
            "run",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    lines = process.stderr.strip().splitlines()
    actual = Result(process.stdout, process.returncode, lines[-1] if lines else "")
    assert_matches(actual, expected, "TypeScript dictionary update protocols")
