"""Exact native combinatorics, integer indexing, and sequential product protocols."""

import json
import subprocess
from pathlib import Path

import pytest

from .conftest import Result, assert_matches, build, run_cpython

_SOURCE = """
import math


def report(name, function, *args, **kwargs):
    try:
        result = function(*args, **kwargs)
        print(name, type(result).__name__, result)
    except Exception as error:
        print(name, type(error).__name__, str(error))


for value in [0, 1, True, 6, 25, 100, -1, -(1 << 100), 1 << 63, 2.0, '3']:
    report('factorial', math.factorial, value)
    report('perm-none', math.perm, value, None)
for values in [(), (0,), (-18,), (-18, 24, 30), (0, 0), (1, 2.0),
               (0, 2.0), (2 ** 200 * 15, 2 ** 180 * 35, 2 ** 160 * 7)]:
    report('gcd', math.gcd, *values)
    report('lcm', math.lcm, *values)
for n, k in [(0, 0), (5, 0), (5, 1), (5, 5), (5, 6), (100, 50),
             (2 ** 100, 2), (2 ** 100, 2 ** 100), (5, 2 ** 100),
             (2 ** 100, 2 ** 90), (-1, 0), (1, -1), (-1, 2.0), (2.0, -1)]:
    report('comb', math.comb, n, k)
    report('perm', math.perm, n, k)


class Index:
    def __init__(self, name, value):
        self.name = name
        self.value = value

    def __index__(self):
        print('index', self.name)
        return self.value

    def __int__(self):
        raise AssertionError('integer math must not call __int__')


class Integer(int):
    def __index__(self):
        raise AssertionError('int subtype must use native integer value')

    def __abs__(self):
        raise AssertionError('integer math must not call subtype __abs__')


report('factorial-index', math.factorial, Index('n', 7))
report('factorial-subtype', math.factorial, Integer(7))
report('gcd-subtype', math.gcd, Integer(-12))
report('lcm-subtype', math.lcm, Integer(-12))
report('gcd-check-all', math.gcd, 1, Index('second', 17), Index('third', 'bad'))
report('lcm-check-all', math.lcm, 0, Index('second', 17), Index('third', 'bad'))
report('comb-index-order', math.comb, Index('n', -1), Index('k', 2))
report('perm-index-order', math.perm, Index('n', -1), Index('k', 'bad'))

for args in [(), (1, 2)]:
    report('factorial-arity', math.factorial, *args)
for args in [(), (1,), (1, 2, 3)]:
    report('comb-arity', math.comb, *args)
for args in [(), (1, 2, 3)]:
    report('perm-arity', math.perm, *args)
report('factorial-keyword', math.factorial, n=3)
report('gcd-keyword', math.gcd, n=3)
report('lcm-keyword', math.lcm, n=3)
report('comb-keyword', math.comb, n=3, k=2)
report('perm-keyword', math.perm, n=3)

for values in [[], [1, 2, 3, 4], [2 ** 80, 2 ** 80, -3],
               [0.5, 4, -2.0], [True, False, 5], [2, 'ab'],
               [0.0, float('inf')], [1.0, 10 ** 1000]]:
    report('prod', math.prod, values)
report('prod-start', math.prod, [2, 3], start=4)
report('prod-string-start', math.prod, [2, 3], start='x')
report('prod-positional-start', math.prod, [2], 3)
report('prod-missing', math.prod)
report('prod-keyword-iterable', math.prod, iterable=[2])
report('prod-unknown-keyword', math.prod, [2], other=3)
report('prod-not-iterable', math.prod, 3)
marker = []
print('empty-start-identity', math.prod([], start=marker) is marker)


class Number:
    def __init__(self, value):
        self.value = value

    def __mul__(self, other):
        print('mul', self.value, other)
        return Number(self.value * other)

    def __rmul__(self, other):
        print('rmul', other, self.value)
        return Number(other * self.value)

    def __repr__(self):
        return 'Number(%s)' % self.value


report('prod-custom-start', math.prod, [2, 3], start=Number(4))
report('prod-reflected', math.prod, [2, Number(3), 4])


class Broken:
    def __rmul__(self, other):
        print('broken multiplication', other)
        raise ValueError('multiply failed')


def stream():
    print('item one')
    yield 2
    print('item two')
    yield Broken()
    print('item three')
    yield 7


iterator = stream()
report('prod-partial', math.prod, iterator)
print('remaining', next(iterator))
"""


def test_ts_integer_math_protocols(tmp_path: Path, ts_vm: list[str]) -> None:
    entry = tmp_path / "integer_math_protocols.py"
    entry.write_text(_SOURCE, encoding="utf-8")
    expected = run_cpython(entry)
    assert expected.code == 0, expected.err_line
    bundle = tmp_path / "integer_math_protocols.dsp.json"
    bundle.write_text(json.dumps(build(entry)), encoding="utf-8")
    process = subprocess.run(
        [*ts_vm, "run", str(bundle)], capture_output=True, text=True, timeout=120
    )
    assert process.returncode == 0, process.stderr
    lines = process.stderr.strip().splitlines()
    result = Result(process.stdout, process.returncode, lines[-1] if lines else "")
    assert_matches(result, expected, "TypeScript integer math protocols")


@pytest.mark.parametrize("operation", ["math.factorial(1000000)", "math.prod(range(1000000))"])
def test_ts_integer_math_charges_fuel(tmp_path: Path, ts_vm: list[str], operation: str) -> None:
    entry = tmp_path / "integer_math_fuel.py"
    entry.write_text(f"import math\n{operation}\n", encoding="utf-8")
    bundle = tmp_path / "integer_math_fuel.dsp.json"
    bundle.write_text(json.dumps(build(entry)), encoding="utf-8")
    process = subprocess.run(
        [*ts_vm, "run", str(bundle), "--max-steps", "100"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert process.returncode != 0
    assert "step budget exceeded (100)" in process.stderr
