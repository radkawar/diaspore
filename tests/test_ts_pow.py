"""Explicit None modulus uses ordinary power arithmetic and special methods."""

import json
import subprocess
from pathlib import Path

from .conftest import Result, assert_matches, build, run_cpython

_SOURCE = """
for base, exponent in [(2, 3), (2.0, 3.0), (4, 0.5), (2, -2),
                       (True, False), (-2, 3), (0, -1), ('x', 2)]:
    for args in [(base, exponent), (base, exponent, None)]:
        try:
            result = pow(*args)
            print(result, type(result).__name__)
        except Exception as error:
            print(type(error).__name__, str(error))


class Power:
    def __pow__(self, other, *modulus):
        print('pow hook', other, modulus)
        return 'power'


class Reflected:
    def __rpow__(self, other, *modulus):
        print('reflected hook', type(other).__name__, modulus)
        return 'reflected'


class Declined:
    def __pow__(self, other, *modulus):
        print('declined hook', type(other).__name__, modulus)
        return NotImplemented


print(pow(Power(), 7), pow(Power(), 7, None))
print(pow(2, Reflected()), pow(2, Reflected(), None))
print(pow(Declined(), Reflected(), None))
for args in [(2, 3, 5), (2.0, 3.0, 5), (2, 3, 0)]:
    try:
        print(pow(*args))
    except Exception as error:
        print(type(error).__name__, str(error))
"""


def test_ts_pow_none_modulus(tmp_path: Path, ts_vm: list[str]) -> None:
    entry = tmp_path / "pow_none_modulus.py"
    entry.write_text(_SOURCE, encoding="utf-8")
    expected = run_cpython(entry)
    assert expected.code == 0, expected.err_line
    bundle = tmp_path / "pow_none_modulus.dsp.json"
    bundle.write_text(json.dumps(build(entry)), encoding="utf-8")
    process = subprocess.run(
        [*ts_vm, "run", str(bundle)], capture_output=True, text=True, timeout=120
    )
    assert process.returncode == 0, process.stderr
    lines = process.stderr.strip().splitlines()
    result = Result(process.stdout, process.returncode, lines[-1] if lines else "")
    assert_matches(result, expected, "TypeScript explicit None power modulus")
