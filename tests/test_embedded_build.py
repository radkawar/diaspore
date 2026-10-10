"""The embedded compilers (diaspore-go build, diaspore-ts build) bundle the stdlib."""

from __future__ import annotations

import subprocess
from pathlib import Path

from .conftest import Result, _last_stderr_line, assert_matches, run_cpython

PROGRAM = """\
import itertools
from collections import Counter, deque
from functools import reduce

import helper

print(list(itertools.accumulate([1, 2, 3, 4])))
print(sorted(Counter("abracadabra").items()))
print(deque([1, 2, 3]).popleft())
print(reduce(lambda a, b: a * b, range(1, 6)))
print(helper.copy.copy([1, 2]))
"""

# A local module named like a stdlib module must shadow the shipped one.
LOCAL_COPY = """\
def copy(value):
    return ["local", *value]
"""

HELPER = "import copy\n"


def _write_program(tmp_path: Path) -> Path:
    entry = tmp_path / "main.py"
    entry.write_text(PROGRAM, encoding="utf-8")
    (tmp_path / "helper.py").write_text(HELPER, encoding="utf-8")
    (tmp_path / "copy.py").write_text(LOCAL_COPY, encoding="utf-8")
    return entry


def _build_and_run(cmd: list[str], entry: Path, tmp_path: Path) -> Result:
    bundle = tmp_path / "main.dsp.json"
    build = subprocess.run(
        [*cmd, "build", str(entry), "-o", str(bundle)], capture_output=True, text=True, timeout=120
    )
    assert build.returncode == 0, build.stderr
    proc = subprocess.run([*cmd, "run", str(bundle)], capture_output=True, text=True, timeout=120)
    return Result(proc.stdout, proc.returncode, _last_stderr_line(proc.stderr, proc.returncode))


def test_go_build_bundles_stdlib(go_vm: Path, tmp_path: Path) -> None:
    entry = _write_program(tmp_path)
    got = _build_and_run([str(go_vm)], entry, tmp_path)
    assert_matches(got, run_cpython(entry), "diaspore-go build")


def test_ts_build_bundles_stdlib(ts_vm: list[str], tmp_path: Path) -> None:
    entry = _write_program(tmp_path)
    got = _build_and_run(ts_vm, entry, tmp_path)
    assert_matches(got, run_cpython(entry), "diaspore-ts build")
