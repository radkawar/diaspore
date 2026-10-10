"""CPython special-function accuracy and reduction protocol behavior."""

import json
import math
import shutil
import subprocess
from pathlib import Path

import pytest

from diaspore import compile_bundle

from .conftest import TS_DIR, Result, assert_matches, run_cpython


def run_ts(tmp_path: Path, source: str) -> tuple[Result, Result]:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js not installed")
    entry = tmp_path / "math_special.py"
    entry.write_text(source, encoding="utf-8")
    expected = run_cpython(entry)
    path = tmp_path / "math_special.dsp.json"
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
    return actual, expected


def test_ts_gamma_accuracy(tmp_path: Path) -> None:
    source = """import math
for x in [-199.5, -170.5, -140.5, -30.25, -9.75, -3.5, -0.5,
          -1e-25, 1e-25, 0.1, 0.5, 1.0, 2.0, 3.0, 7.0, 23.0,
          23.5, 70.25, 139.75, 140.5, 170.5]:
    print(math.gamma(x).hex(), math.lgamma(x).hex())
"""
    actual, expected = run_ts(tmp_path, source)
    assert expected.code == 0, expected.err_line
    assert actual.code == 0, actual.err_line
    wanted = expected.stdout.splitlines()
    obtained = actual.stdout.splitlines()
    assert len(obtained) == len(wanted)
    for actual_line, expected_line in zip(obtained, wanted, strict=True):
        for actual_hex, expected_hex in zip(
            actual_line.split(),
            expected_line.split(),
            strict=True,
        ):
            actual_value = float.fromhex(actual_hex)
            expected_value = float.fromhex(expected_hex)
            # CPython documents a ten-ulp bound for its portable Lanczos algorithm.
            assert actual_value == pytest.approx(
                expected_value,
                rel=0,
                abs=10 * math.ulp(expected_value),
            )


@pytest.mark.parametrize(
    "source",
    [
        """import math
for values in [[], [-0.0], [1e16, 1.0, -1e16], [1e-16, 1.0, 1e16],
               [1e100, 1.0, -1e100], [0.1] * 10,
               [2.0**-1074] * 3, [1.0, 2.0**-53, 2.0**-100]]:
    print(math.fsum(values).hex(), math.fsum(reversed(values)).hex())
for values in [(), (-0.0,), (3, 4), (1, 1, 1), (1e308, 1e308),
               (1e-300, 1e-300), (2.0**-1074, 2.0**-1074),
               (1.0, 2.0**-27), (float('nan'), float('inf'))]:
    print(math.hypot(*values).hex())
for left, right in [([], []), ([3, 4], [0, 0]), ([1, 2, 3], [4, 5, 6]),
                    ([1e308], [-1e308]), ([float('inf')], [float('inf')]),
                    ([float('nan'), float('inf')], [0, 0])]:
    print(math.dist(left, right).hex())
""",
        """import math
for name in ['gamma', 'lgamma']:
    function = getattr(math, name)
    for value in [0.0, -0.0, -1.0, -2.0, float('inf'), float('-inf'),
                  float('nan'), 1e308, 2.0**-1074, -201.5, -202.5, '1', 10**1000]:
        try:
            print(name, function(value).hex())
        except (ValueError, OverflowError, TypeError) as error:
            print(name, type(error).__name__, str(error))
for values in [[float('inf'), float('-inf')], [float('nan'), float('inf')],
               [1e308, 1e308, -1e308], [float('nan'), float('inf'), float('-inf')]]:
    try:
        print(math.fsum(values).hex())
    except (ValueError, OverflowError) as error:
        print(type(error).__name__, str(error))
try:
    math.dist([1], [1, 2])
except ValueError as error:
    print(type(error).__name__, str(error))
""",
        """import math
class Floatable:
    def __init__(self, label, value):
        self.label = label
        self.value = value
    def __float__(self):
        print('float', self.label)
        return self.value
class Indexed:
    def __index__(self):
        print('index')
        return 5
print(math.gamma(Indexed()))
print(math.fsum([Floatable('one', 1.0), Indexed(), Floatable('two', 2.0)]))
def point(label, values):
    print('start', label)
    for value in values:
        print('yield', label)
        yield Floatable(label, value)
    print('end', label)
print(math.dist(point('left', [3.0, 4.0]), point('right', [0.0, 0.0])))
try:
    math.dist(point('short', [1.0]), point('long', [2.0, 3.0]))
except ValueError as error:
    print(type(error).__name__, str(error))
class Problem(Exception):
    pass
def failing():
    yield Floatable('before', 1.0)
    raise Problem('iterator failure')
try:
    math.fsum(failing())
except Problem as error:
    print(type(error).__name__, str(error))
class BadFloat:
    def __float__(self):
        return 1
for function in [math.gamma, math.lgamma]:
    try:
        function(BadFloat())
    except TypeError as error:
        print(type(error).__name__, str(error))
""",
    ],
    ids=["accurate-sums-and-norms", "special-values-and-errors", "numeric-protocols"],
)
def test_ts_special_math_semantics(tmp_path: Path, source: str) -> None:
    actual, expected = run_ts(tmp_path, source)
    assert expected.code == 0, expected.err_line
    assert_matches(actual, expected, "TypeScript special math")
