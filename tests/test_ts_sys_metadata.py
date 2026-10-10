"""Native numeric metadata exposes real representation and structsequence behavior."""

import json
import random
import subprocess
from pathlib import Path

from .conftest import TS_DIR, Result, assert_matches, build, run_cpython

_SOURCE = """
import sys

info = sys.float_info
print(info)
print(isinstance(info, tuple), len(info), info[0], info[-1], info[1:3])
print(tuple(info))
print(type(info).__name__, type(info).__module__)
print(info.n_fields, info.n_sequence_fields, info.n_unnamed_fields)
print(hasattr(info, '__dict__'))
print(info.max == float.fromhex('0x1.fffffffffffffp+1023'))
print(info.min == float.fromhex('0x1.0000000000000p-1022'))
print(info.epsilon == 2.0 ** -52, info.mant_dig, info.dig)
print(info.max_exp, info.max_10_exp, info.min_exp, info.min_10_exp)
print(info.radix, info.rounds)
for name in ['max', 'epsilon', 'rounds']:
    try:
        setattr(info, name, 3)
    except AttributeError as error:
        print(type(error).__name__, str(error))
    try:
        delattr(info, name)
    except AttributeError as error:
        print(type(error).__name__, str(error))
cls = type(info)
print(cls(tuple(info)) == info)
print(cls(sequence=tuple(info), dict={}) == info)
for size in [10, 12]:
    try:
        cls([1] * size)
    except TypeError as error:
        print(type(error).__name__, str(error))
constructor, args = info.__reduce__()
print(constructor is cls, constructor(*args) == info)
try:
    class Child(cls):
        pass
except TypeError as error:
    print(type(error).__name__, str(error))
"""


def test_ts_float_info(tmp_path: Path, ts_vm: list[str]) -> None:
    entry = tmp_path / "float_metadata.py"
    entry.write_text(_SOURCE, encoding="utf-8")
    expected = run_cpython(entry)
    assert expected.code == 0, expected.err_line
    bundle = tmp_path / "float_metadata.dsp.json"
    bundle.write_text(json.dumps(build(entry)), encoding="utf-8")
    process = subprocess.run(
        [*ts_vm, "run", str(bundle)], capture_output=True, text=True, timeout=120
    )
    assert process.returncode == 0, process.stderr
    lines = process.stderr.strip().splitlines()
    result = Result(process.stdout, process.returncode, lines[-1] if lines else "")
    assert_matches(result, expected, "TypeScript sys.float_info")


def test_ts_hex_float_rounding(tmp_path: Path, ts_vm: list[str]) -> None:
    values = [
        "0",
        "-0",
        "0x1",
        ".8",
        "1.",
        "  -0X1.Ap+2\t",
        "inf",
        "-Infinity",
        "+nan",
        "0x1.fffffffffffffp+1023",
        "0x1.fffffffffffff7p+1023",
        "0x1.fffffffffffff8p+1023",
        "0x1p1024",
        "0x1p-1074",
        "0x1p-1075",
        "0x1.0000000000001p-1075",
        "0x3p-1075",
        "-0x1p-1076",
        "0x0.fffffffffffff8p-1022",
        "0x1.00000000000008p0",
        "0x1.00000000000018p0",
        "0x0p999999999999999999999",
        "1p999999999999999999999",
        "1p-999999999999999999999",
        "",
        ".",
        "0x",
        "1p",
        "1p+",
        "0x1_0",
        "0x1p2junk",
        "\u00a01",
        "1\u00a0",
        "\0",
        "\ud800",
    ]
    rng = random.Random(48391)
    for _ in range(300):
        coefficient = rng.getrandbits(rng.randrange(1, 257))
        exponent = rng.randrange(-1400, 1100)
        values.append(f"{'-' if rng.randrange(2) else ''}0x{coefficient:x}p{exponent}")
    expected = []
    for value in values:
        try:
            expected.append(["value", float.fromhex(value).hex()])
        except (ValueError, OverflowError, UnicodeError) as error:
            expected.append([type(error).__name__, str(error)])
    fixture = tmp_path / "hex_float.json"
    fixture.write_text(json.dumps(values), encoding="utf-8")
    script = r"""
import {readFileSync} from "node:fs";
import {parseFloatHex, formatFloatHex} from "./src/numeric.ts";
import {DiasporeError} from "./src/values.ts";
import {Interp} from "./src/interp.ts";
const I = new Interp({diaspore:1, main:"m", modules:{m:{name:"m", file:"m.py", body:[]}}});
const values = JSON.parse(readFileSync(process.argv[1], "utf8"));
const results = values.map(value => {
  try { return ["value", formatFloatHex(parseFloatHex(value))]; }
  catch (error) {
    if (!(error instanceof DiasporeError)) throw error;
    return [error.value.cls.name, I.drive(I.str(error.value))];
  }
});
console.log(JSON.stringify(results));
"""
    process = subprocess.run(
        [ts_vm[0], "--input-type=module", "-e", script, str(fixture)],
        cwd=TS_DIR,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert process.returncode == 0, process.stderr
    assert json.loads(process.stdout) == expected


_DESCRIPTOR_SOURCE = """
def report(label, function, *args, **kwargs):
    try:
        value = function(*args, **kwargs)
        print(label, type(value).__name__, value)
    except Exception as error:
        print(label, type(error).__name__, str(error))


for value in [None, 1, b'1', [], object()]:
    report('nonstr', float.fromhex, value)
report('fromhex-missing', float.fromhex)
report('fromhex-extra', float.fromhex, '1', '2')
report('fromhex-keyword', float.fromhex, string='1')
report('fromhex-keyword-extra', float.fromhex, '1', other='2')


class Text(str):
    def __str__(self):
        return 'not hexadecimal'


report('str-subclass', float.fromhex, Text('0x1.8p1'))


class Number(float):
    def __new__(cls, value):
        print('new-number', cls.__name__, type(value).__name__, value)
        return float.__new__(cls, value + 1.0)
    def __float__(self):
        raise AssertionError('must read float payload directly')


number = Number.fromhex('0x1.8p1')
print('subtype', type(number) is Number, number.hex(), float.hex(number))
other = number.fromhex('0x1p2')
print('instance-fromhex', type(other) is Number, other.hex())
report('float-instance-fromhex', (1.0).fromhex, '0x1p-1')
report('hex-int', float.hex, 1)
report('hex-missing', float.hex)
report('hex-extra', float.hex, 1.0, 2.0)
report('hex-keyword', float.hex, 1.0, other=2)
report('bound-hex-keyword', number.hex, other=2)
"""


def test_ts_float_hex_descriptors(tmp_path: Path, ts_vm: list[str]) -> None:
    entry = tmp_path / "float_hex_descriptors.py"
    entry.write_text(_DESCRIPTOR_SOURCE, encoding="utf-8")
    expected = run_cpython(entry)
    assert expected.code == 0, expected.err_line
    bundle = tmp_path / "float_hex_descriptors.dsp.json"
    bundle.write_text(json.dumps(build(entry)), encoding="utf-8")
    process = subprocess.run(
        [*ts_vm, "run", str(bundle)], capture_output=True, text=True, timeout=120
    )
    assert process.returncode == 0, process.stderr
    lines = process.stderr.strip().splitlines()
    result = Result(process.stdout, process.returncode, lines[-1] if lines else "")
    assert_matches(result, expected, "TypeScript float hexadecimal descriptors")


_INTEGER_HASH_SOURCE = """
import sys

fields = [
    ('int_info', ('bits_per_digit', 'sizeof_digit', 'default_max_str_digits',
                  'str_digits_check_threshold')),
    ('hash_info', ('width', 'modulus', 'inf', 'nan', 'imag', 'algorithm',
                   'hash_bits', 'seed_bits', 'cutoff')),
]
for name, names in fields:
    info = getattr(sys, name)
    cls = type(info)
    print(cls.__name__, cls.__module__, isinstance(info, tuple), len(info))
    print(info.n_fields, info.n_sequence_fields, info.n_unnamed_fields)
    print(all(getattr(info, field) == info[index] for index, field in enumerate(names)))
    print(hasattr(info, '__dict__'))
    values = tuple(range(len(names)))
    fresh = cls(values)
    print(fresh, tuple(fresh), fresh == values, fresh[-1], fresh[1:3])
    print(cls(sequence=values), cls(values, {}), cls.__new__(cls, values))
    constructor, args = fresh.__reduce__()
    print(constructor is cls, args, constructor(*args) == fresh)
    for invoke in [lambda: cls(()), lambda: cls(values + (9,)),
                   lambda: cls(), lambda: cls(values, None),
                   lambda: cls(values, sequence=values),
                   lambda: setattr(info, names[0], 99),
                   lambda: delattr(info, names[0]),
                   lambda: setattr(info, 'new_attribute', 99)]:
        try:
            invoke()
        except Exception as error:
            print(type(error).__name__)
    try:
        class Derived(cls):
            pass
    except TypeError as error:
        print(type(error).__name__, str(error))
"""


def test_ts_integer_hash_structsequences(tmp_path: Path, ts_vm: list[str]) -> None:
    entry = tmp_path / "integer_hash_structsequences.py"
    entry.write_text(_INTEGER_HASH_SOURCE, encoding="utf-8")
    expected = run_cpython(entry)
    assert expected.code == 0, expected.err_line
    bundle = tmp_path / "integer_hash_structsequences.dsp.json"
    bundle.write_text(json.dumps(build(entry)), encoding="utf-8")
    process = subprocess.run(
        [*ts_vm, "run", str(bundle)], capture_output=True, text=True, timeout=120
    )
    assert process.returncode == 0, process.stderr
    lines = process.stderr.strip().splitlines()
    result = Result(process.stdout, process.returncode, lines[-1] if lines else "")
    assert_matches(result, expected, "TypeScript integer/hash structsequences")


_HASH_RUNTIME_SOURCE = """
import sys


def fnv(text):
    value = 0xcbf29ce484222325
    for char in text:
        value = ((value ^ ord(char)) * 0x100000001b3) & ((1 << 64) - 1)
    return value & ((1 << 63) - 1)


integer = sys.int_info
assert integer.bits_per_digit in (32, 64)
assert integer.sizeof_digit * 8 == integer.bits_per_digit
assert integer.default_max_str_digits == integer.str_digits_check_threshold == 0
assert len(str(10 ** 4999)) == 5000
assert len(str(int('9' * 5000))) == 5000
info = sys.hash_info
assert info.width == 64 and info.modulus == (1 << 61) - 1
assert info.algorithm == 'fnv1a64-63'
assert info.hash_bits == 63 and info.seed_bits == info.cutoff == 0
assert info.inf == hash(float('inf')) == -hash(float('-inf'))
assert info.nan == hash(float('nan')) == fnv('fnan')
assert info.imag == 1000003
assert hash(3 + 7j) == hash(3) + info.imag * hash(7)
assert hash(10 ** 100) == (10 ** 100) % info.modulus
assert hash(0.5) == (info.modulus + 1) // 2
for text in ['', 'abc', 'Hash metadata']:
    assert hash(text) == fnv('s' + text)
    assert hash(text.encode('ascii')) == fnv('b' + text)
print('native metadata matches representation and hashing')
"""


def test_ts_hash_metadata_matches_runtime(tmp_path: Path, ts_vm: list[str]) -> None:
    """Algorithm-specific values deliberately report TS behavior, not host SipHash."""
    entry = tmp_path / "hash_metadata_behavior.py"
    entry.write_text(_HASH_RUNTIME_SOURCE, encoding="utf-8")
    bundle = tmp_path / "hash_metadata_behavior.dsp.json"
    bundle.write_text(json.dumps(build(entry)), encoding="utf-8")
    process = subprocess.run(
        [*ts_vm, "run", str(bundle)], capture_output=True, text=True, timeout=120
    )
    assert process.returncode == 0, process.stderr
    assert process.stdout == "native metadata matches representation and hashing\n"
    assert process.stderr == ""
