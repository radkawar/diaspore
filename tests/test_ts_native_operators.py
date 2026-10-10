"""Native TypeScript operators, sets and match indexing follow CPython."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from diaspore import compile_bundle

from .conftest import TS_DIR, Result, assert_matches, run_cpython

_INDEX_SOURCE = """import _operator


class IntValue(int):
    def __index__(self):
        raise AssertionError('integer subclasses use their stored value')


class IndexValue:
    def __init__(self, value):
        self.value = value
    def __index__(self):
        print('index callback')
        return self.value


class IntOnly:
    def __int__(self):
        raise AssertionError('__int__ is not the index protocol')


class RaisingIndex:
    def __index__(self):
        raise ValueError('index failed')


for value in [False, True, 0, -7, 2 ** 200, -(2 ** 200), IntValue(19),
              IndexValue(23), IndexValue(2 ** 200), IndexValue(True),
              IndexValue(IntValue(29))]:
    result = _operator.index(value)
    print(result, type(result) is int)

shadow = IndexValue(31)
shadow.__index__ = lambda: 99
print('class lookup', _operator.index(shadow))
for value in [1.5, '3', None, IntOnly(), IndexValue('bad'), IndexValue(1.5),
              IndexValue(None), RaisingIndex()]:
    try:
        _operator.index(value)
    except Exception as error:
        print(type(error).__name__, str(error))
for args, kwargs in [((), {}), ((1, 2), {}), ((), {'a': 1})]:
    try:
        _operator.index(*args, **kwargs)
    except TypeError:
        print('index arity')
"""

_ADD_SOURCE = """import _operator


class Base:
    def __add__(self, other):
        print('base add')
        return 'base result'
    def __radd__(self, other):
        print('base reflected')
        return 'base reflected result'


class Sub(Base):
    def __radd__(self, other):
        print('sub reflected')
        return 'sub result'


class RefusingSub(Base):
    def __radd__(self, other):
        print('sub declines')
        return NotImplemented


class Declines:
    def __add__(self, other):
        print('left declines')
        return NotImplemented


class Reflected:
    def __radd__(self, other):
        print('right reflected')
        return 'reflected result'


class BothDecline:
    def __add__(self, other):
        print('both add declines')
        return NotImplemented
    def __radd__(self, other):
        print('both reflected declines')
        return NotImplemented


class Raising:
    def __add__(self, other):
        print('raising add')
        raise ValueError('add failed')


print(_operator.add(Base(), Sub()))
print(_operator.add(Base(), RefusingSub()))
print(_operator.add(Base(), Reflected()))
print(_operator.add(Declines(), Reflected()))
print(_operator.add(3, Reflected()))
for left, right in [(BothDecline(), BothDecline()), (Declines(), BothDecline()),
                    (Raising(), Reflected()), (1, 'x')]:
    try:
        print(_operator.add(left, right))
    except Exception as error:
        print(type(error).__name__, str(error))
print(_operator.add(True, 2), _operator.add(2 ** 200, 2 ** 200))
print(_operator.add('left', 'right'))
left = [1]
right = [2, 3]
result = _operator.add(left, right)
print(result, left, right, result is left, result is right)
for args, kwargs in [((), {}), ((1,), {}), ((1, 2, 3), {}),
                     ((), {'a': 1, 'b': 2})]:
    try:
        _operator.add(*args, **kwargs)
    except TypeError:
        print('add arity')
"""

_SET_POP_SOURCE = """class Key:
    blocked = False
    def __hash__(self):
        if self.blocked:
            raise AssertionError('pop must not hash the stored key')
        print('key hash')
        return 7
    def __eq__(self, other):
        raise AssertionError('pop must not compare the stored key')


key = Key()
values = {key}
key.blocked = True
print(values.pop() is key, len(values), bool(values))
try:
    values.pop()
except KeyError as error:
    print(type(error).__name__, error.args, str(error))
key.blocked = False
values.add(key)
key.blocked = True
print(set.pop(values) is key, len(values))
for args, kwargs in [((1,), {}), ((), {'default': 1})]:
    values = {5}
    try:
        values.pop(*args, **kwargs)
    except TypeError:
        print('pop arity', len(values), 5 in values)
print('pop' in set.__dict__, hasattr(frozenset(), 'pop'))
"""

_MATCH_SOURCE = r"""import re


class Index:
    def __index__(self):
        print('group index')
        return 1


class BadIndex:
    def __index__(self):
        return 'bad'


class RaisingIndex:
    def __index__(self):
        raise ValueError('group index failed')


class GroupNumber(int):
    pass


class GroupName(str):
    pass


for pattern, text in [(r'(?P<first>a)(b)?', 'a'),
                      (br'(?P<first>a)(b)?', b'a'),
                      (r'(?P<first>..)(x)?', '\ud800\udc00')]:
    match = re.match(pattern, text)
    print(ascii(match[0]), ascii(match[1]), match[2])
    for key in [0, 1, 2, False, True, 'first', GroupNumber(1),
                GroupName('first'), Index()]:
        print(ascii(match[key]), ascii(match.group(key)))
    for key in [-1, 3, 2 ** 200, 'missing', b'first', 1.5, None,
                (1,), slice(0, 1), BadIndex(), RaisingIndex()]:
        for operation in [match.__getitem__, match.group]:
            try:
                operation(key)
            except Exception as error:
                print(type(error).__name__, str(error))
    for args, kwargs in [((), {}), ((0, 1), {}), ((), {'group': 0})]:
        try:
            match.__getitem__(*args, **kwargs)
        except TypeError:
            print('match item arity')
"""

_STRING_METHOD_SOURCE = r"""class Count:
    def __index__(self):
        return 1


for value, sep in [(' a  b c ', None), ('a:b:c', ':'), ('aaaaa', 'aa'),
                   ('x\ud800\udc00y\ud800\udc00z', '\ud800\udc00')]:
    for method in [value.split, value.rsplit]:
        print(ascii(method(sep=sep, maxsplit=Count())))
        print(ascii(method(sep, maxsplit=0)))
        print(ascii(method(maxsplit=-1, sep=sep)))
        for args, kwargs in [((sep,), {'sep': sep}),
                             ((sep, 1), {'maxsplit': 2}),
                             ((), {'invalid': 1}), ((), {'maxsplit': None})]:
            try:
                method(*args, **kwargs)
            except TypeError as error:
                print(type(error).__name__, str(error))

for first, second in [(b'+/', b'-_'), (bytearray(b'aba'), memoryview(b'xyz')),
                      (b'', b'')]:
    table = bytes.maketrans(first, second)
    print(len(table), table.hex(), b'+/aba?'.translate(table))
    print(b'+/aba?'.translate(table, delete=b'a?'))
    print(b'+/aba?'.translate(table, bytearray(b'+/')))
    print(b''.maketrans(first, second) == table)


class TranslationBytes(bytes):
    pass


class TranslationArray(bytearray):
    pass


print(isinstance(bytes.__dict__['maketrans'], staticmethod),
      isinstance(bytearray.__dict__['maketrans'], staticmethod))
expected_table = bytes.maketrans(b'+/', b'-_')
for receiver in [bytes, b'', TranslationBytes, TranslationBytes(),
                 bytearray, bytearray(), TranslationArray, TranslationArray()]:
    table = receiver.maketrans(b'+/', b'-_')
    print(type(table) is bytes, table == expected_table,
          table is receiver.maketrans(b'+/', b'-_'))
    print(receiver.maketrans is receiver.maketrans)
    for args, kwargs in [((b'a', b'bc'), {}), (('a', b'b'), {}),
                         ((b'a',), {}), ((b'a', b'b'), {'extra': b'c'})]:
        try:
            receiver.maketrans(*args, **kwargs)
        except Exception as error:
            print(type(error).__name__)
print(b'abcabc'.translate(None), b'abcabc'.translate(None, delete=memoryview(b'b')))
for args, kwargs in [((b'a', b'bc'), {}), (('a', b'b'), {}),
                     ((b'a', 'b'), {}), ((b'a',), {}),
                     ((b'a', b'b'), {'extra': b'c'})]:
    try:
        bytes.maketrans(*args, **kwargs)
    except Exception as error:
        print(type(error).__name__)
for args, kwargs in [((b'short',), {}), ((None, None), {}),
                     ((None, b'a'), {'delete': b'b'}), ((), {'table': None})]:
    try:
        b'abc'.translate(*args, **kwargs)
    except Exception as error:
        print(type(error).__name__)

for value in ['', '0129', '\u0661\uff12', '\U0001d7d9', '\u00b2', '\u2167',
              '1.0', '\ud800\udc00', ' 1', 'abc']:
    print(ascii(value), value.isdecimal())
for args, kwargs in [((1,), {}), ((), {'extra': 1})]:
    try:
        '1'.isdecimal(*args, **kwargs)
    except TypeError:
        print('decimal arity')

for value in [b'ab', bytearray(b'ab')]:
    for name in ['ljust', 'rjust', 'center']:
        method = getattr(value, name)
        for width in [-1, 0, 2, 3, 4, 5, Count()]:
            for fill in [b' ', b'\x00', bytearray(b'-')]:
                result = method(width, fill)
                print(name, type(result).__name__, result, result is value)
        for args, kwargs in [((3, b''), {}), ((3, b'xx'), {}),
                             ((3, memoryview(b'-')), {}), ((3, '-'), {}),
                             ((None,), {}), ((2 ** 100,), {}),
                             ((), {'width': 3}), ((3,), {'fillbyte': b'-'})]:
            try:
                method(*args, **kwargs)
            except Exception as error:
                print(name, type(error).__name__)

for value in ['', '\t', 'ab\tcd\t', 'a\tb\nc\td\re\tf', 'a\tb\r\nc\td',
              '\U00010000\t', '\ud800\udc00\t', 'e\u0301\t', '\v\t\f\t']:
    print(ascii(value.expandtabs()))
    for width in [-2, 0, 1, 4, 8, Count()]:
        print(ascii(value.expandtabs(width)), ascii(value.expandtabs(tabsize=width)))
for args, kwargs in [((1, 2), {}), ((1,), {'tabsize': 2}),
                     ((), {'invalid': 1}), ((None,), {}), ((1.5,), {}),
                     ((2 ** 40,), {}), ((-(2 ** 40),), {})]:
    try:
        'a\tb'.expandtabs(*args, **kwargs)
    except Exception as error:
        print('expandtabs', type(error).__name__)
"""

_SET_UPDATE_SOURCE = """names = ['update', 'difference_update', 'intersection_update',
         'symmetric_difference_update']
for name in names:
    values = {1, 2, 3}
    alias = values
    result = getattr(values, name)([2, 2, 4])
    print(name, result is None, values is alias, sorted(values))
    values = {1, 2}
    print('self', name, getattr(values, name)(values), sorted(values))
    print('immutable', hasattr(frozenset(), name))
for name in names[:-1]:
    values = {1, 2, 3}
    print('empty', name, getattr(values, name)(), sorted(values))
    print('multiple', name, getattr(values, name)([2, 4], (3, 4)), sorted(values))


def failing(values):
    yield 2
    print('partial state', sorted(values))
    raise ValueError('iteration failed')


for name in names:
    values = {1, 2, 3}
    try:
        getattr(values, name)(failing(values))
    except ValueError as error:
        print(name, str(error), sorted(values))
    values = {1, 2, 3}
    try:
        getattr(values, name)([2, []])
    except TypeError:
        print('unhashable', name, sorted(values))
    try:
        getattr(values, name)(other=[2])
    except TypeError:
        print('keyword', name)
for name in names[:-1]:
    values = {1, 2, 3}
    try:
        getattr(values, name)([2], failing(values))
    except ValueError:
        print('later failure', name, sorted(values))

values = {1}
iterator = iter(values)
try:
    values.difference_update(iterator)
except RuntimeError as error:
    print('self iterator', str(error), len(values))
try:
    next(iterator)
except RuntimeError:
    print('iterator remains invalid')
for args in [(), ([1], [2])]:
    try:
        set().symmetric_difference_update(*args)
    except TypeError:
        print('symmetric arity')


class Stored:
    blocked = False
    def __init__(self, tag):
        self.tag = tag
    def __hash__(self):
        if self.blocked:
            raise AssertionError('stored keys must not be rehashed')
        return self.tag
    def __eq__(self, other):
        return self is other


for name in names:
    first, shared, last = Stored(1), Stored(2), Stored(3)
    values = {first, shared}
    other = frozenset([shared, last])
    first.blocked = shared.blocked = last.blocked = True
    print('cached hash', name, getattr(values, name)(other),
          sorted(item.tag for item in values))
for name in ['update', 'symmetric_difference_update']:
    key = Stored(7)
    other = {key: None}
    key.blocked = True
    values = set()
    print('dict cached hash', name, getattr(values, name)(other),
          next(iter(values)) is key)


class SetInput(set):
    def __iter__(self):
        raise AssertionError('set inputs use stored entries')


class DictInput(dict):
    def __iter__(self):
        yield 9


for name in names:
    values = {1, 2}
    print('set subclass', name, getattr(values, name)(SetInput([2, 3])),
          sorted(values))
for name in ['update', 'symmetric_difference_update']:
    values = {1}
    print('dict subclass', name, getattr(values, name)(DictInput({2: 0})),
          sorted(values))
"""

_SPLITLINES_SOURCE = r"""class Ends:
    def __index__(self):
        return 1


class TruthOnly:
    def __bool__(self):
        raise AssertionError('keepends uses the index protocol')


for value in ['', '\n', '\r\n', 'a\r\nb\rc\n', 'a\n\nb\n',
              'a\vb\fc\x1cd\x1de\x1ef\x85g\u2028h\u2029',
              '\ud800\udc00\n\U00010000\r\n']:
    print(ascii(value.splitlines()))
    for keep in [False, True, 0, -1, 2, Ends()]:
        print(ascii(value.splitlines(keep)), ascii(value.splitlines(keepends=keep)))
for args, kwargs in [((True, False), {}), ((True,), {'keepends': False}),
                     ((), {'invalid': True}), ((None,), {}), ((1.5,), {}),
                     ((TruthOnly(),), {}), ((2 ** 40,), {})]:
    try:
        'first\nsecond\n'.splitlines(*args, **kwargs)
    except Exception as error:
        print(type(error).__name__)
"""

_FROMHEX_SOURCE = r"""class HexText(str):
    def __str__(self):
        raise AssertionError('fromhex uses the string payload')
    def __iter__(self):
        raise AssertionError('fromhex does not iterate the string subclass')


for receiver in [bytes, b'', bytearray, bytearray()]:
    for text in ['', ' ', '00', 'abcdefABCDEF', '00 01\t02\n03\r04\v05\f06',
                 HexText('41 42')]:
        value = receiver.fromhex(text)
        print(type(value).__name__, value.hex())
    for text in ['0', ' 0', '0 ', '0\t0', '00 z0', '00\x0011', '00\x1c11',
                 '00\u00a011', 'zz\u00e9', '00\U00010000', '00\ud800\udc00']:
        try:
            receiver.fromhex(text)
        except Exception as error:
            print(type(error).__name__, str(error))
    for args, kwargs in [((b'00',), {}), ((None,), {}), ((), {}),
                         (('00', '11'), {}), ((), {'string': '00'})]:
        try:
            receiver.fromhex(*args, **kwargs)
        except TypeError as error:
            print(type(error).__name__, str(error))


class BytesChild(bytes):
    def __new__(cls, value):
        print('bytes new', cls.__name__, type(value).__name__, value.hex())
        return bytes.__new__(cls, value)
    def __init__(self, value):
        print('bytes init', type(value).__name__, value.hex())


class ArrayChild(bytearray):
    def __new__(cls, value):
        print('array new', cls.__name__, type(value).__name__, value.hex())
        return bytearray.__new__(cls, value)
    def __init__(self, value):
        print('array init', type(value).__name__, value.hex())
        bytearray.__init__(self, value)


for cls in [BytesChild, ArrayChild]:
    result = cls.fromhex('41 42')
    print(type(result) is cls, result.hex())
    result = result.fromhex('43')
    print(type(result) is cls, result.hex())
    for receiver in [cls, result]:
        for args, kwargs in [((None,), {}), ((), {}), (('00', '11'), {}),
                             ((), {'string': '00'})]:
            try:
                receiver.fromhex(*args, **kwargs)
            except TypeError as error:
                print(type(error).__name__, str(error))


class ForeignBytes(bytes):
    def __new__(cls, value):
        return ('foreign', type(value).__name__, value.hex())
    def __init__(self, value):
        raise AssertionError('foreign result must skip initialization')


class ForeignArray(bytearray):
    def __new__(cls, value):
        return ('foreign', type(value).__name__, value.hex())
    def __init__(self, value):
        raise AssertionError('foreign result must skip initialization')


print(ForeignBytes.fromhex('44'), ForeignArray.fromhex('45'))
"""

_BYTES_ASCII_SOURCE = r"""class ByteChild(bytes):
    def __iter__(self):
        raise AssertionError('isascii inspects stored bytes')


class ArrayChild(bytearray):
    def __iter__(self):
        raise AssertionError('isascii inspects stored bytes')


for cls in [bytes, bytearray, ByteChild, ArrayChild]:
    for source in [b'', b'\x00\t\n\r\x7f', b'plain ASCII', b'\x80',
                   b'ab\xffcd', bytes(range(128)), bytes(range(256))]:
        value = cls(source)
        print(cls.__name__, value.isascii(), cls.isascii(value))
        print(type(value.isascii()) is bool)
        for args, kwargs in [((None,), {}), ((), {'extra': True})]:
            try:
                value.isascii(*args, **kwargs)
            except TypeError:
                print('isascii arity')
for cls in [bytes, bytearray]:
    descriptor = cls.__dict__['isascii']
    print(type(descriptor).__name__)
    value = cls(b'ascii')
    print(descriptor(value), descriptor.__get__(value, cls)())
    for wrong in [None, 'ascii', 1, memoryview(b'ascii'),
                  bytearray() if cls is bytes else b'']:
        try:
            descriptor(wrong)
        except TypeError:
            print('isascii receiver')
value = bytearray(b'A')
print(value.isascii())
value[0] = 128
print(value.isascii())
"""


@pytest.mark.parametrize(
    "source",
    [
        _INDEX_SOURCE,
        _ADD_SOURCE,
        _SET_POP_SOURCE,
        _MATCH_SOURCE,
        _STRING_METHOD_SOURCE,
        _SET_UPDATE_SOURCE,
        _SPLITLINES_SOURCE,
        _FROMHEX_SOURCE,
        _BYTES_ASCII_SOURCE,
    ],
    ids=[
        "index",
        "add",
        "set-pop",
        "match-item",
        "string-methods",
        "set-update",
        "splitlines",
        "fromhex",
        "bytes-ascii",
    ],
)
def test_ts_native_operator_protocols(tmp_path: Path, source: str) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js not installed")
    entry = tmp_path / "native_operators.py"
    entry.write_text(source, encoding="utf-8")
    expected = run_cpython(entry)
    assert expected.code == 0, expected.err_line
    bundle = compile_bundle(str(entry), stdlib=False)
    assert "stdlib" not in bundle
    assert list(bundle["modules"]) == ["native_operators"]
    bundle_path = tmp_path / "native_operators.dsp.json"
    bundle_path.write_text(json.dumps(bundle), encoding="utf-8")
    process = subprocess.run(
        [
            node,
            "--experimental-strip-types",
            "--no-warnings",
            str(TS_DIR / "src/cli.ts"),
            "run",
            str(bundle_path),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    lines = process.stderr.strip().splitlines()
    actual = Result(process.stdout, process.returncode, lines[-1] if lines else "")
    assert_matches(actual, expected, "TypeScript native operator protocols")
