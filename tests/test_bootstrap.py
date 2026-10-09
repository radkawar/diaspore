"""Run the package compiler and reference VM inside each Diaspore runtime."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from diaspore import Machine, compile_bundle

from .conftest import ROOT, Result, assert_matches

FIXTURES = ROOT / "tests" / "bootstrap"
DRIVER = FIXTURES / "driver.py"


@pytest.fixture(scope="session")
def bootstrap_bundle() -> dict:
    return compile_bundle(str(DRIVER), search_paths=[str(ROOT / "src")])


@pytest.fixture(scope="session")
def bootstrap_bundle_path(bootstrap_bundle: dict, tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("bootstrap") / "driver.dsp.json"
    path.write_text(json.dumps(bootstrap_bundle), encoding="utf-8")
    return path


@pytest.fixture(scope="session", params=["core.py", "generator.py"])
def bootstrap_case(request) -> tuple[str, Result]:
    case = request.param
    proc = subprocess.run(
        [sys.executable, str(DRIVER), case], cwd=ROOT, input="",
        capture_output=True, text=True, timeout=120,
        env=dict(os.environ, PYTHONHASHSEED="0", PYTHONDONTWRITEBYTECODE="1"),
    )
    assert proc.returncode == 0, proc.stderr
    return case, Result(proc.stdout, proc.returncode, "")


@pytest.mark.parametrize("runtime", ["py", "go", "ts", "lua"])
def test_package_bootstrap(runtime, bootstrap_case, bootstrap_bundle,
                           bootstrap_bundle_path, request, record_property):
    case, expected = bootstrap_case
    if case == "generator.py" and runtime in ("ts", "lua"):
        pytest.skip("inner generators run on tasks, which diaspore-ts and diaspore-lua lack")
    capabilities = {"fs"}
    if case == "generator.py":
        capabilities.add("spawn")
    if runtime == "py":
        start = time.perf_counter()
        out, err = io.StringIO(), io.StringIO()
        machine = Machine(
            bootstrap_bundle, argv=[str(DRIVER), case], caps=capabilities,
            policy={"fs": {"read": [str(ROOT)], "write": []}},
            stdout=out, stderr=err,
        )
        code = machine.run()
        actual = Result(out.getvalue(), code, err.getvalue())
    else:
        if runtime == "go":
            command = [str(request.getfixturevalue("go_vm"))]
        else:
            command = list(request.getfixturevalue(runtime + "_vm"))
        command += ["run", str(bootstrap_bundle_path), "--allow-fs", "read:" + str(ROOT)]
        if "spawn" in capabilities:
            command.append("--allow-spawn")
        command += ["--", case]
        start = time.perf_counter()
        proc = subprocess.run(command, cwd=ROOT, input="", capture_output=True,
                              text=True, timeout=300)
        actual = Result(proc.stdout, proc.returncode, proc.stderr)
    elapsed = time.perf_counter() - start
    record_property("runtime_seconds", elapsed)
    print(f"bootstrap {runtime}/{case}: {elapsed:.3f}s")
    assert actual.code == 0, actual.err_line
    assert_matches(actual, expected, "nested diaspore-" + runtime)
