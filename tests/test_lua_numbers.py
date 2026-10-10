"""Lua arbitrary-precision integer integration against unchanged CPython semantics."""

from pathlib import Path

import pytest

from .conftest import assert_matches, run_cpython, run_diaspore_lua

_CASES = {
    "arithmetic": r"""
lo = -9223372036854775808
hi = 9223372036854775807
for a in [lo - 1, lo, lo + 1, -1, 0, 1, hi - 1, hi, hi + 1, 2 ** 140 + 19]:
    print(a, -a, abs(a), ~a, a + 1, a - 1, a * 3)
    for b in [-2 ** 70 - 3, -3, -1, 1, 3, 2 ** 70 + 3]:
        print(a + b, a - b, a * b, a // b, a % b, divmod(a, b))
        print(a & b, a | b, a ^ b)
    for shift in [0, 1, 62, 63, 64, 129]:
        print(a << shift, a >> shift)
print((hi + 1) - 1, (lo - 1) + 1, lo // -1, lo % -1)
print(pow(3, 201, 2 ** 90 + 7), pow(3, -7, 2 ** 90 + 7))
print(pow(-3, 101), pow(2, -1074), (2 ** 2000) / (2 ** 1999))
print(True & False, True | False, True ^ True)
""",
    "zero_division": r"""
for n in [1, -1, 2 ** 140, -(2 ** 140)]:
    for operation in [lambda: n % 0, lambda: n // 0, lambda: divmod(n, 0)]:
        try:
            operation()
        except ZeroDivisionError as error:
            print(type(error).__name__, str(error))
""",
    "comparison_hash": r"""
for a in [-(2 ** 1100), -2 ** 100, -2 ** 63, -1, 0, 1, 2 ** 53 + 1,
          2 ** 61 - 1, 2 ** 63 - 1, 2 ** 63, 2 ** 100, 2 ** 1100]:
    print(a, hash(a), type(a), isinstance(a, int), bool(a), bool(a - a))
    for b in [float('-inf'), -1e100, -0.5, -0.0, 0.5, 9007199254740992.0,
              9223372036854775808.0, 1.2676506002282294e30, float('inf'), float('nan')]:
        print(a == b, a != b, a < b, a <= b, a > b, a >= b)
for value in [0.0, -0.0, -1.0, 0.5, -1.25, 1e100, 2.0 ** -1074, float('inf')]:
    print(hash(value))
big = 2 ** 100
for a in [big - 1, big, big + 1, 2 ** 1100]:
    print(a == complex(float(big), 0), complex(float(big), 0) == a)
d = {big: 'integer', float(big): 'float', big + 1: 'neighbor'}
print(d, len(d), d[big], d[float(big)], len({big, float(big), big + 1}))
print(hash(big) == hash(float(big)), hash(big) == hash(complex(float(big), 0)))
print(sorted([big, -big, big + 1, 0, 9223372036854775807]))
""",
    "range_index": r"""
big = 2 ** 100
for r in [range(big, big + 7, 2), range(-big, -big - 7, -2),
          range(-2 ** 63, 2 ** 63, 2 ** 62), range(big, big + 1, big)]:
    print(r, len(r), list(r), list(reversed(r)), bool(r))
    print(r[0], r[-1], r[1:], r[::-1], r[::big], r[::-big])
    print(r.start in r, r.stop in r, float(r.start) in r)
r = range(-big, big, 3)
print(bool(r), r[big // 3], r[-1], r[big // 3:big // 3 + 3])
print(range(big, big + 1) == range(big, big + 2, 2))
print(hash(range(big, big + 1)) == hash(range(big, big + 2, 2)))
print(list(enumerate(['a', 'b', 'c'], 9223372036854775807)))
print(list(enumerate(['a', 'b'], big)))
print([1, 2, 3][-big:big], [1, 2, 3][::big], [1, 2, 3][::-big])
print(slice(-big, big, big).indices(3), slice(None, None, -big).indices(big))
for action in [lambda: len(r), lambda: [1][big], lambda: [1][-big],
               lambda: 'a' * big, lambda: [1] * -big]:
    try:
        action()
    except (OverflowError, IndexError) as error:
        print(type(error).__name__)
""",
    "conversions": r"""
import json
for n in [-2 ** 200, -2 ** 63, 2 ** 63 - 1, 2 ** 63, 2 ** 200 + 123]:
    print(str(n), repr(n), bin(n), oct(n), hex(n), float(n), int(float(n)))
    print(n.bit_length(), n.bit_count(), n.numerator, n.denominator, n.real, n.imag)
    print(format(n, '+040x'), format(n, ',d'), format(n, '#b'))
    print(int(str(n)), int(hex(n), 0), json.loads(json.dumps(n)))
for text in ['１２３４５６７８９０１２３４５６７８９０', '١٢٣٤٥٦٧٨٩٠', '\u2003123\u2003']:
    print(int(text), float(text))
for value in [1e100, -1e100, 1.7976931348623157e308, -0.0]:
    print(int(value), float(int(value)))
for action in [lambda: float(2 ** 1100), lambda: (2 ** 1100) + 1.0,
               lambda: (2 ** 1100) / 1, lambda: (2 ** 1100) ** -1,
               lambda: int(float('inf')), lambda: int(float('nan'))]:
    try:
        action()
    except (OverflowError, ValueError) as error:
        print(type(error).__name__, str(error))
import math
print(pow(2, 100, None), round(1e100), math.floor(1e100), math.ceil(-1e100))
for n in [2 ** 100 + 5, 10 ** 40 + 5, 10 ** 40 + 15, -(10 ** 40 + 5), -(10 ** 40 + 15)]:
    print(round(n, -1), round(n, -30), round(n, 20))
print(math.ldexp(1.0, -(2 ** 100)))
try:
    math.sqrt(2 ** 1100)
except OverflowError as error:
    print(type(error).__name__, str(error))
print(format(1.25, '.110f'), format(1e-100, '.110e'), format(float('inf'), '.110g'))
print(format(-0.0, 'z.2f'), format(1.0, '#.0F'))
for a, b in [(1, 0.1), (-1, 0.1), (0, -1.0), (1, float('inf')), (-1, float('inf'))]:
    print(a // b, a % b, divmod(a, b))
""",
    "float_info": r"""
import sys
info = sys.float_info
names = ['max', 'max_exp', 'max_10_exp', 'min', 'min_exp', 'min_10_exp',
         'dig', 'mant_dig', 'epsilon', 'radix', 'rounds']
print(info, repr(info), type(info), type(info).__name__, type(info).__module__)
print(isinstance(info, tuple), len(info), tuple(info), info[0], info[-1], info[2:5])
print(info.n_fields, info.n_sequence_fields, info.n_unnamed_fields)
print(info == tuple(info), hash(info) == hash(tuple(info)), hasattr(info, '__dict__'))
for index, name in enumerate(names):
    print(name, getattr(info, name), getattr(info, name) == info[index])
for name in names + ['extra', 'n_fields']:
    try:
        setattr(info, name, 0)
    except AttributeError as error:
        print(type(error).__name__, str(error))
    try:
        delattr(info, name)
    except AttributeError as error:
        print(type(error).__name__, str(error))
try:
    object.__setattr__(info, 'max', 0)
except AttributeError as error:
    print(type(error).__name__, str(error))
print(info.max, info.min, info.epsilon)
""",
    "complex_hash": r"""
for real in [0.0, -0.0, 1.0, -1.0, 0.5, -1.25, 1e100, 2.0 ** -1074, float('inf')]:
    for imag in [0.0, -0.0, 1.0, -1.0, 0.5, -1.25, 1e100, float('-inf')]:
        value = complex(real, imag)
        print(value, hash(value))
print(hash(complex(-1000004, 1)))
print(hash(complex(2 ** 100, 0)) == hash(2 ** 100))
""",
}


@pytest.mark.parametrize("case", _CASES)
def test_lua_integer_paths(case: str, tmp_path: Path, lua_vm: list[str]) -> None:
    entry = tmp_path / "integers.py"
    entry.write_text(_CASES[case], encoding="utf-8")
    expected = run_cpython(entry)
    assert expected.code == 0, expected.err_line
    assert_matches(run_diaspore_lua(entry, lua_vm, tmp_path), expected, f"Lua integers: {case}")


def test_lua_numeric_metadata(tmp_path: Path, lua_vm: list[str]) -> None:
    entry = tmp_path / "numeric_metadata.py"
    entry.write_text(
        r"""
import sys
integer = sys.int_info
hashes = sys.hash_info
assert tuple(integer) == (24, 8, 0, 0)
assert tuple(hashes) == (64, 2 ** 61 - 1, 314159, hash(float('nan')),
                         1000003, 'fnv1a64', 64, 0, 0)
assert hashes.modulus == hash(2 ** 61 - 2) + 1
assert hashes.inf == hash(float('inf'))
assert hashes.imag == hash(1j)
h = 14695981039346656037
for byte in b'sabc':
    h = ((h ^ byte) * 1099511628211) & (2 ** 64 - 1)
assert hash('abc') == h & (2 ** 63 - 1)
for name, info, fields in [
    ('int_info', integer, ['bits_per_digit', 'sizeof_digit', 'default_max_str_digits',
                           'str_digits_check_threshold']),
    ('hash_info', hashes, ['width', 'modulus', 'inf', 'nan', 'imag', 'algorithm',
                          'hash_bits', 'seed_bits', 'cutoff']),
]:
    assert type(info).__name__ == name
    assert type(info).__module__ == 'sys'
    assert isinstance(info, tuple) and len(info) == len(fields)
    assert info.n_fields == info.n_sequence_fields == len(fields)
    assert info.n_unnamed_fields == 0
    assert info == tuple(info) and hash(info) == hash(tuple(info))
    assert not hasattr(info, '__dict__')
    assert repr(info) == 'sys.' + name + '(' + ', '.join(
        field + '=' + repr(getattr(info, field)) for field in fields) + ')'
    for index, field in enumerate(fields):
        assert getattr(info, field) == info[index]
        try:
            setattr(info, field, 0)
        except AttributeError as error:
            assert str(error) == 'readonly attribute'
        else:
            raise AssertionError('numeric field accepted assignment')
        try:
            delattr(info, field)
        except AttributeError as error:
            assert str(error) == 'readonly attribute'
        else:
            raise AssertionError('numeric field accepted deletion')
print('numeric metadata ok')
""",
        encoding="utf-8",
    )
    result = run_diaspore_lua(entry, lua_vm, tmp_path)
    assert result.code == 0, result.err_line
    assert result.stdout == "numeric metadata ok\n"
