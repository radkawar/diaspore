"""Native real mathematics follows CPython conversion and IEEE boundaries."""

import json
import subprocess
from pathlib import Path

from .conftest import Result, assert_matches, build, run_cpython

_SOURCE = r"""
import math

print(math.__doc__)
for name in ['acos', 'asin', 'atan', 'sin', 'cos', 'tan', 'sqrt', 'exp', 'expm1',
             'log1p', 'degrees', 'radians', 'fabs']:
    function = getattr(math, name)
    print(name, [round(function(x), 12) for x in [0.0, 0.25, 0.5, 1.0]])
print('atan2', [round(math.atan2(y, x), 12) for y, x in [(0, -1), (-0.0, -1), (1, 0), (-1, 0)]])
print('logs', math.log(8, 2), math.log2(2 ** 2000), round(math.log10(10 ** 400), 12))
print('predicates', [(math.isfinite(x), math.isinf(x), math.isnan(x))
                     for x in [0, 1.5, math.inf, -math.inf, math.nan]])
for x in [0.0, -0.0, 1.0, -3.25, float.fromhex('0x1p-1074'), float.fromhex('0x1p-1022'),
          float.fromhex('0x1.fffffffffffffp+1023'), math.inf, -math.inf, math.nan]:
    mantissa, exponent = math.frexp(x)
    print('frexp', mantissa.hex(), exponent, math.ldexp(mantissa, exponent).hex())
    print('ulp', math.ulp(x).hex())
for x, exponent in [(1.0, -1074), (1.5, -1074), (2.5, -1074), (-1.0, -10000),
                    (float.fromhex('0x1p-1074'), 2097), (1.0, 1023)]:
    print('ldexp', math.ldexp(x, exponent).hex())
for x, y in [(1.0, 2.0), (0.0, -1.0), (-0.0, 1.0), (-0.0, 0.0), (math.inf, 0.0),
             (-float.fromhex('0x1p-1074'), 1.0)]:
    for steps in [0, 1, 2, 10, 2 ** 100]:
        print('nextafter', math.nextafter(x, y, steps=steps).hex())
for x, y in [(3.0, 2.0), (5.0, 2.0), (7.0, 2.0), (-5.0, 2.0), (-0.0, 2.0),
             (float.fromhex('0x1p-1074'), float.fromhex('0x1.8p-1073')), (4.0, math.inf)]:
    print('remainder', math.remainder(x, y).hex(), math.fmod(x, y).hex())
for x, y in [(1.0, -0.0), (-1.0, 0.0), (0.0, -1.0), (1.0, -math.nan)]:
    print('copysign', math.copysign(x, y).hex())
for x, y in [(0.0, -math.inf), (-1.0, math.inf), (1.0, math.nan), (math.nan, 0.0)]:
    print('pow', math.pow(x, y).hex())
print('close', math.isclose(1, 1 + 1e-10), math.isclose(a=1, b=2, abs_tol=1),
      math.isclose(math.inf, math.inf), math.isclose(math.inf, -math.inf),
      math.isclose(math.nan, math.nan))

class Floating:
    def __float__(self):
        return 0.5

class Indexed:
    def __index__(self):
        return 2

class BadFloat:
    def __float__(self):
        return 1

class Rounder:
    def __floor__(self):
        return 'floor'
    def __ceil__(self):
        return 'ceil'
    def __trunc__(self):
        return 'trunc'

class FloatSubclass(float):
    def __float__(self):
        raise AssertionError('float payload should be used')
    def __floor__(self):
        return 17

print('protocols', math.sqrt(Indexed()), math.fabs(Floating()), math.fabs(FloatSubclass(-2.0)))
print('round protocols', math.floor(Rounder()), math.ceil(Rounder()),
      math.trunc(Rounder()), math.floor(FloatSubclass(2.0)))
print('integer round', math.floor(2 ** 100), math.ceil(True), math.trunc(-3.75))
print('nextafter protocols', math.nextafter(1.0, 2.0, steps=None).hex(),
      math.nextafter(1.0, 2.0, steps=Indexed()).hex(),
      math.nextafter(-1.0, 1.0, steps=2 ** 100).hex())

cases = [
    ('sqrt', (-1,)), ('sin', (math.inf,)), ('acos', (2,)), ('log1p', (-1,)),
    ('exp', (1000,)), ('pow', (0, -1)), ('pow', (-1, 0.5)), ('pow', (1e300, 2)),
    ('ldexp', (1.0, 10000)), ('ldexp', (1.0, Indexed())), ('remainder', (1, 0)),
    ('remainder', (math.inf, 1)), ('fmod', (1, 0)), ('floor', (math.inf,)),
    ('trunc', (math.nan,)), ('trunc', (Floating(),)), ('sqrt', ('4',)),
    ('sqrt', (BadFloat(),)), ('sqrt', (10 ** 1000,)), ('log', (2, 1)),
]
for name, args in cases:
    try:
        getattr(math, name)(*args)
    except Exception as error:
        print('error', name, type(error).__name__, str(error))
try:
    math.nextafter(1, 2, steps=-1)
except Exception as error:
    print('error nextafter', type(error).__name__, str(error))
try:
    math.isclose(1, 2, rel_tol=-1)
except Exception as error:
    print('error isclose', type(error).__name__, str(error))
"""


def test_ts_real_math(tmp_path: Path, ts_vm: list[str]) -> None:
    entry = tmp_path / "real_math.py"
    entry.write_text(_SOURCE, encoding="utf-8")
    expected = run_cpython(entry)
    assert expected.code == 0, expected.err_line
    bundle = tmp_path / "real_math.dsp.json"
    bundle.write_text(json.dumps(build(entry)), encoding="utf-8")
    process = subprocess.run(
        [*ts_vm, "run", str(bundle)], capture_output=True, text=True, timeout=120
    )
    assert process.returncode == 0, process.stderr
    lines = process.stderr.strip().splitlines()
    result = Result(process.stdout, process.returncode, lines[-1] if lines else "")
    assert_matches(result, expected, "TypeScript real math")
