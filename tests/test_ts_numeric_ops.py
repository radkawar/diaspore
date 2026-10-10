"""Mixed integer/float arithmetic preserves Python conversion boundaries."""

import json
import subprocess
from pathlib import Path

from .conftest import Result, assert_matches, build, run_cpython

_SOURCE = """
huge = 10 ** 1000
operations = [
    ('add', lambda a, b: a + b), ('sub', lambda a, b: a - b),
    ('mul', lambda a, b: a * b), ('div', lambda a, b: a / b),
    ('floordiv', lambda a, b: a // b), ('mod', lambda a, b: a % b),
    ('pow', lambda a, b: a ** b), ('divmod', divmod),
]
for name, operation in operations:
    for value in [1.0, 0.0, float('inf'), float('nan')]:
        for left, right in [(huge, value), (value, huge), (-huge, value)]:
            try:
                operation(left, right)
            except Exception as error:
                print(name, type(error).__name__, str(error))
for operation in [lambda a, b: a << b, lambda a, b: a >> b,
                  lambda a, b: a & b, lambda a, b: a | b, lambda a, b: a ^ b]:
    for left, right in [(huge, 1.0), (1.0, huge)]:
        try:
            operation(left, right)
        except Exception as error:
            print(type(error).__name__, str(error))
print('comparisons', huge > 1.0, huge < float('inf'), huge == float('inf'))
print('ordinary', 3 + 1.5, 3 * 1.5, 3 / 1.5, 3 ** 1.0)
"""


def test_ts_mixed_numeric_overflow(tmp_path: Path, ts_vm: list[str]) -> None:
    entry = tmp_path / "numeric_ops.py"
    entry.write_text(_SOURCE, encoding="utf-8")
    expected = run_cpython(entry)
    assert expected.code == 0, expected.err_line
    bundle = tmp_path / "numeric_ops.dsp.json"
    bundle.write_text(json.dumps(build(entry)), encoding="utf-8")
    process = subprocess.run(
        [*ts_vm, "run", str(bundle)], capture_output=True, text=True, timeout=120
    )
    assert process.returncode == 0, process.stderr
    lines = process.stderr.strip().splitlines()
    result = Result(process.stdout, process.returncode, lines[-1] if lines else "")
    assert_matches(result, expected, "TypeScript mixed numeric arithmetic")
