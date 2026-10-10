"""Lua builtin methods and host-sized argument boundaries match CPython."""

import unicodedata
from pathlib import Path

import pytest

from .conftest import assert_matches, run_cpython, run_diaspore_lua

_CASES = {
    "integer_methods": """
for value in [0, True, -1, -(1 << 63), (1 << 63) - 1, 1 << 63,
              (1 << 200) + 257, -((1 << 200) + 257)]:
    print(value.bit_length(), value.bit_count())
    print(value.real, value.imag, value.numerator, value.denominator)
    print(value.conjugate(), value.as_integer_ratio(), value.is_integer())
    print(int.bit_length(value), int.bit_count(value), int.__index__(value))
    for order in ['big', 'little']:
        for signed in [False, True]:
            if value < 0 and not signed:
                continue
            data = value.to_bytes(32, order, signed=signed)
            print(data.hex(), int.from_bytes(data, order, signed=signed) == value)
for value in [0, -1, 1, 128, 255, 256, -128, -129, 1 << 64]:
    for length in [0, 1, 8]:
        for signed in [False, True]:
            try:
                print(value.to_bytes(length, signed=signed).hex())
            except OverflowError:
                print('OverflowError')
print(int.from_bytes([255] * 64), int.from_bytes([255] * 64, signed=True))
class Number(int):
    pass
n = Number(1 << 100)
print(n.bit_length(), n.bit_count(), int.bit_length(n), n.to_bytes(13).hex())
for action in [lambda: (1).bit_length(2), lambda: int.bit_count('x'),
               lambda: (1).bit_count(extra=True)]:
    try:
        action()
    except TypeError:
        print('TypeError')
""",
    "buffer_values": """
b = bytearray(8)
view = memoryview(b).cast('Q')
for value in [0, (1 << 63) - 1, 1 << 63, (1 << 64) - 1]:
    view[0] = value
    print(view[0], int.from_bytes(b, 'little'))
for value in [-1, 1 << 64, 1 << 200]:
    try:
        view[0] = value
    except ValueError:
        print('ValueError')
signed = memoryview(b).cast('q')
for value in [-(1 << 63), (1 << 63) - 1]:
    signed[0] = value
    print(signed[0])
for value in [-(1 << 63) - 1, 1 << 63]:
    try:
        signed[0] = value
    except ValueError:
        print('ValueError')
floats = memoryview(b).cast('d')
floats[0] = 1 << 100
print(floats[0] == float(1 << 100))
for value in [1 << 100, -(1 << 100)]:
    try:
        bytearray([value])
    except ValueError:
        print('ValueError')
    try:
        b[value]
    except IndexError:
        print('IndexError')
    try:
        view[value]
    except IndexError:
        print('IndexError')
view = memoryview(b'1234').cast('H')
for step in [1 << 100, -(1 << 100), (1 << 63) - 1, -(1 << 63)]:
    print(view[::step].tolist(), view[::step].strides)
print(bytearray(b'abc')[::1 << 100], bytearray(b'abc')[::-1 << 100])
""",
    "hex_grouping": """
for data in [b'abcde', bytearray(b'abcde'), memoryview(b'abcde')]:
    for group in [-2147483648, -3, -2, -1, 0, 1, 2, 3, 2147483647]:
        print(data.hex(':', group), data.hex(sep=b' ', bytes_per_sep=group))
    for group in [1 << 100, -(1 << 100), 1 << 31]:
        try:
            data.hex(':', group)
        except OverflowError:
            print('OverflowError')
    for sep in ['', 'ab', 'é', bytes([255])]:
        try:
            data.hex(sep)
        except ValueError:
            print('ValueError')
print(bytes.hex(b'abc', '-', 2))
""",
    "host_bounds": """
huge = 1 << 100
for bound in [huge, -huge]:
    for action in [lambda: 'a b'.split(None, bound),
                   lambda: 'a'.replace('a', 'b', bound),
                   lambda: b'a'.replace(b'a', b'b', bound),
                   lambda: 'a'.center(bound), lambda: 'a'.ljust(bound),
                   lambda: 'a'.rjust(bound), lambda: 'a'.zfill(bound),
                   lambda: [1].insert(bound, 2), lambda: [1].pop(bound),
                   lambda: bytearray(b'a').insert(bound, 2),
                   lambda: (1).to_bytes(bound),
                   lambda: memoryview(b'a').cast('B', [bound])]:
        try:
            action()
        except OverflowError:
            print('OverflowError')
    print('abc'.find('', bound), 'abc'.count('', bound))
    print('abc'.startswith('', bound), 'abc'.endswith('', bound))
    print('abc'.find('a', bound), 'abc'.find('', 0, bound))
    print([1, 2, 3].index(2, -huge, huge))
print('a'.center(-(1 << 63)), 'a'.ljust(-(1 << 63)), 'a'.rjust(-(1 << 63)))
""",
    "regex_bounds": """
import re
huge = 1 << 100
pattern = re.compile('(a)')
for bound in [huge, -huge]:
    for action in [lambda: pattern.search('a', bound),
                   lambda: pattern.search('a', 0, bound),
                   lambda: pattern.sub('b', 'a', bound),
                   lambda: pattern.split('a', bound),
                   lambda: re.compile('a', bound)]:
        try:
            action()
        except OverflowError:
            print('OverflowError')
    try:
        pattern.search('a').group(bound)
    except IndexError:
        print('IndexError')
print(pattern.search('a', (1 << 63) - 1))
print(pattern.search('a', -(1 << 63)).group())
print(pattern.sub('b', 'a', -1), pattern.split('a', -1))
""",
    "split_signatures": """
for text in ['', '  a  b c  ', 'aaa', 'ééé', 'a::b::']:
    for sep in [None, ' ', 'aa', 'éé', '::']:
        for count in [-1, 0, 1, 2]:
            print(text.split(sep=sep, maxsplit=count), text.rsplit(maxsplit=count, sep=sep))
for cls in [bytes, bytearray]:
    for text in [b'', b'  a  b c  ', b'aaa', b'a::b::']:
        for sep in [None, b' ', b'aa', b'::']:
            for count in [-1, 0, 1, 2]:
                value = cls(text)
                print(value.split(sep=sep, maxsplit=count), value.rsplit(maxsplit=count, sep=sep))
for action in [lambda: 'a'.split(None, sep=None), lambda: b'a'.rsplit(None, -1, maxsplit=2),
               lambda: 'a'.split(other=2), lambda: b'a'.split(sep='a')]:
    try:
        action()
    except TypeError:
        print('TypeError')
""",
    "byte_translation_join": """
for cls in [bytes, bytearray]:
    table = cls.maketrans(b'aba', bytearray(b'XYZ'))
    print(type(table).__name__, len(table), table[97], table[98])
    value = cls(b'abcaba')
    print(value.translate(table), value.translate(table, delete=memoryview(b'b')))
    print(value.translate(None, b'a'), value.translate(None))
    print(cls(b'-').join([b'a', bytearray(b'b'), memoryview(b'c')]))
    print(cls(b'-').join([]), cls(b'-').join(part for part in [b'a', b'b']))
    print(type(value.maketrans(b'a', b'b')).__name__)
    for action in [lambda: cls.maketrans(b'a', b''), lambda: value.translate(b'bad')]:
        try:
            action()
        except ValueError:
            print('ValueError')
    for action in [lambda: cls.maketrans('a', 'b'), lambda: value.translate(table=table),
                   lambda: value.translate(None, b'a', delete=b'a'), lambda: cls(b'').join([1])]:
        try:
            action()
        except TypeError:
            print('TypeError')
original = bytearray(b'abc')
copy = bytearray.copy(original)
copy[0] = 100
print(original, copy)
""",
    "byte_padding_decimal": """
for cls in [bytes, bytearray]:
    for text in [b'', b'abc', b'-12', b'+7']:
        value = cls(text)
        for width in [-2, 0, 3, 4, 5, 6]:
            print(value.ljust(width), value.rjust(width, bytearray(b'!')),
                  value.center(width, b'*'), value.zfill(width))
    for width in [1 << 100, -(1 << 100)]:
        try:
            cls(b'a').ljust(width)
        except OverflowError:
            print('OverflowError')
    try:
        cls(b'a').ljust(4, memoryview(b'*'))
    except TypeError:
        print('TypeError')
for text in ['', '123', '123a', '١٢٣', '１２３', '²', '½', chr(0x1D7CE), chr(0xD800)]:
    print(text.isdecimal(), str.isdecimal(text))
for action in [lambda: '1'.isdecimal(1), lambda: '1'.isdecimal(extra=True)]:
    try:
        action()
    except TypeError:
        print('TypeError')
""",
    "set_pop": """
items = set(range(100))
removed = []
while items:
    removed.append(items.pop())
print(sorted(removed) == list(range(100)), len(items))
for value in [False, None, 1 << 100, 'a']:
    items = {value}
    print(items.pop() == value, len(items))
class Key:
    def __init__(self):
        self.calls = 0
    def __hash__(self):
        self.calls += 1
        return 42
key = Key()
items = {key}
print(set.pop(items) is key, key.calls)
try:
    set().pop()
except KeyError as error:
    print(str(error))
for action in [lambda: {1}.pop(1), lambda: {1}.pop(extra=True)]:
    try:
        action()
    except TypeError:
        print('TypeError')
print(hasattr(frozenset([1]), 'pop'))
""",
    "expandtabs": r"""
for text in ['', 'plain text', '\t', 'a\tb\t', '1234\t', 'é\t😀\t',
             'a\u0301\tb', '\t\r\t\n\t', 'a\v\tb\f\t', chr(0xD800) + '\t']:
    for size in [-2147483648, -1, 0, 1, 2, 4, 8, False, True]:
        print(repr(text.expandtabs(size)), repr(str.expandtabs(text, tabsize=size)))
print(repr('a\tb'.expandtabs()), 'plain'.expandtabs(2147483647))
for size in [2147483648, -2147483649, 1 << 100]:
    try:
        'plain'.expandtabs(size)
    except OverflowError:
        print('OverflowError')
for action in [lambda: ''.expandtabs(1.0), lambda: ''.expandtabs(None),
               lambda: ''.expandtabs(1, tabsize=2), lambda: ''.expandtabs(other=2)]:
    try:
        action()
    except TypeError:
        print('TypeError')
""",
    "set_difference_update": """
values = set(range(10))
alias = values
print(values.difference_update(), len(values))
print(set.difference_update(values, [1, 3, 3], (5, 7), {9: 'nine'}),
      sorted(values), alias is values)
print(values.difference_update(values), len(values), alias is values)
values = {1, 2, 3}
try:
    values.difference_update([2, []])
except TypeError:
    print('TypeError', sorted(values))
try:
    values.difference_update([1], None)
except TypeError:
    print('TypeError', sorted(values))
class Key:
    def __init__(self):
        self.calls = 0
    def __hash__(self):
        self.calls += 1
        return 42
key = Key()
left, right = {key}, {key}
print(left.difference_update(right), len(left), key.calls)
class NativeSet(set):
    def __iter__(self):
        raise ValueError('must use native set storage')
values = {1, 2, 3}
print(values.difference_update(NativeSet([2])), sorted(values))
values = {1, 2, 3}
try:
    values.difference_update(iter(values))
except RuntimeError:
    print('RuntimeError', len(values))
try:
    values.difference_update(other=[1])
except TypeError:
    print('TypeError')
print(hasattr(frozenset([1]), 'difference_update'))
""",
    "splitlines_keepends": r"""
for separator in ['\n', '\r', '\r\n', '\v', '\f', '\x1c', '\x1d', '\x1e',
                  '\x85', '\u2028', '\u2029', '\x1f', '\0']:
    for text in ['', separator, 'a' + separator, separator + 'b',
                 'é' + separator + chr(0xD800) + separator + '😀']:
        for keepends in [False, True, None, 0, 1.0, -1, 1 << 100, [], [1]]:
            print(repr(text.splitlines(keepends)), repr(str.splitlines(text, keepends=keepends)))
print(repr('a\r\n\r\nb\r\r\n'.splitlines(True)))
print(repr('first\nsecond\n'.splitlines(True)))
class Index:
    def __index__(self):
        return 0
class Truth:
    def __bool__(self):
        return False
print(repr('a\nb'.splitlines(Index())), repr('a\nb'.splitlines(Truth())))
for action in [lambda: ''.splitlines(True, False), lambda: ''.splitlines(True, keepends=False),
               lambda: ''.splitlines(other=True)]:
    try:
        action()
    except TypeError:
        print('TypeError')
""",
}


@pytest.mark.parametrize("case", _CASES)
def test_lua_numeric_methods(case: str, tmp_path: Path, lua_vm: list[str]) -> None:
    entry = tmp_path / "numeric_methods.py"
    entry.write_text(_CASES[case], encoding="utf-8")
    expected = run_cpython(entry)
    assert expected.code == 0, expected.err_line
    assert_matches(
        run_diaspore_lua(entry, lua_vm, tmp_path), expected, f"Lua numeric methods: {case}"
    )


def test_lua_isprintable(tmp_path: Path, lua_vm: list[str]) -> None:
    points = set(range(128))
    previous = ""
    for code in range(128, 0x110000):
        category = unicodedata.category(chr(code))
        if category != previous:
            points.update((code - 1, code))
            if code < 0x10FFFF:
                points.add(code + 1)
            previous = category
    entry = tmp_path / "printable.py"
    entry.write_text(
        "points = " + repr(sorted(points)) + "\n"
        "for code in points:\n"
        "    text = chr(code)\n"
        "    print(code, text.isprintable(), str.isprintable(text), ('a' + text).isprintable())\n"
        "print(''.isprintable(), 'Hello, world!'.isprintable(), ' '.isprintable())\n"
        "class Text(str):\n"
        "    pass\n"
        "print(Text('hello').isprintable(), Text(chr(0xD800)).isprintable())\n"
        "for action in [lambda: 'a'.isprintable(1), lambda: 'a'.isprintable(extra=True)]:\n"
        "    try:\n"
        "        action()\n"
        "    except TypeError:\n"
        "        print('TypeError')\n",
        encoding="utf-8",
    )
    expected = run_cpython(entry)
    assert expected.code == 0, expected.err_line
    assert_matches(run_diaspore_lua(entry, lua_vm, tmp_path), expected, "Lua str.isprintable")
