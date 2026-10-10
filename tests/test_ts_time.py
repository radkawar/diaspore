"""Native performance counters share the monotonic and deterministic clocks."""

import json
import subprocess
from pathlib import Path

from .conftest import Result, assert_matches, build, run_cpython


def test_ts_performance_counter(tmp_path: Path, ts_vm: list[str]) -> None:
    entry = tmp_path / "performance_counter.py"
    entry.write_text(
        """
import time
before = time.perf_counter()
time.sleep(0.003)
after = time.perf_counter()
print(type(before).__name__, after >= before, after - before >= 0.001)
print(time.perf_counter.__name__)
for args, kwargs in [((1,), {}), ((), {'value': 1})]:
    try:
        time.perf_counter(*args, **kwargs)
    except Exception as error:
        print(type(error).__name__, str(error))
""",
        encoding="utf-8",
    )
    expected = run_cpython(entry)
    assert expected.code == 0, expected.err_line
    bundle = tmp_path / "performance_counter.dsp.json"
    bundle.write_text(json.dumps(build(entry)), encoding="utf-8")
    process = subprocess.run(
        [*ts_vm, "run", str(bundle)], capture_output=True, text=True, timeout=120
    )
    assert process.returncode == 0, process.stderr
    assert_matches(Result(process.stdout, process.returncode, ""), expected, "TS perf_counter")


def test_ts_performance_counter_virtual_clock(tmp_path: Path, ts_vm: list[str]) -> None:
    entry = tmp_path / "virtual_clock.py"
    entry.write_text(
        """
import time
print(time.time(), time.monotonic(), time.perf_counter())
time.sleep(0.25)
print(time.perf_counter(), time.monotonic(), time.time())
""",
        encoding="utf-8",
    )
    bundle = tmp_path / "virtual_clock.dsp.json"
    bundle.write_text(json.dumps(build(entry)), encoding="utf-8")
    process = subprocess.run(
        [*ts_vm, "run", str(bundle), "--deterministic"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert process.returncode == 0, process.stderr
    assert process.stdout == "0.001 0.002 0.003\n0.254 0.255 0.256\n"
