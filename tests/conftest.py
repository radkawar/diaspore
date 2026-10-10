"""Corpus conformance harness.

Ground truth is CPython itself: every corpus program is written in the diaspore
subset, so `python3 program.py` defines the expected stdout, exit code, and
(for failing programs) the final stderr line. All four runtimes must match byte-for-byte.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "tests" / "corpus"
RUNTIMES = ROOT / "runtimes"
GO_DIR = RUNTIMES / "go"
GO_BIN = ROOT / "build" / "diaspore-go"
TS_DIR = RUNTIMES / "ts"
LUA_DIR = RUNTIMES / "lua"

sys.path.insert(0, str(ROOT / "src"))

from diaspore.compiler import compile_bundle  # noqa: E402
from diaspore.validator import validate_bundle  # noqa: E402
from diaspore.vm import Machine  # noqa: E402


@dataclass
class Result:
    stdout: str
    code: int
    err_line: str  # last non-empty stderr line ("" when exit code is 0)


def _scan(root: Path) -> list[Path]:
    entries: list[Path] = []
    if not root.exists():
        return entries
    for p in sorted(root.iterdir()):
        if p.suffix == ".py":
            entries.append(p)
        elif p.is_dir() and (p / "main.py").exists():
            entries.append(p / "main.py")
    return entries


def corpus_entries() -> list[Path]:
    return _scan(CORPUS)


def _last_stderr_line(stderr: str, code: int) -> str:
    if code == 0:
        return ""
    lines = [ln for ln in stderr.splitlines() if ln.strip()]
    return lines[-1] if lines else ""


def run_cpython(entry: Path) -> Result:
    env = dict(os.environ, PYTHONHASHSEED="0", PYTHONDONTWRITEBYTECODE="1")
    proc = subprocess.run(
        [sys.executable, str(entry)],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
        input="",
    )
    return Result(proc.stdout, proc.returncode, _last_stderr_line(proc.stderr, proc.returncode))


def caps_for(entry: Path) -> set[str]:
    """Programs opt into capabilities via a `# diaspore-caps: spawn,net` header."""
    for line in entry.read_text(encoding="utf-8").splitlines()[:5]:
        if line.startswith("# diaspore-caps:"):
            return {c.strip() for c in line.split(":", 1)[1].split(",") if c.strip()}
    return set()


def build(entry: Path) -> dict:
    bundle = compile_bundle(str(entry))
    validate_bundle(bundle)
    return bundle


def run_diaspore_py(entry: Path, max_steps: int | None = None) -> Result:
    out, err = io.StringIO(), io.StringIO()
    m = Machine(
        build(entry),
        argv=[str(entry)],
        caps=caps_for(entry),
        max_steps=max_steps,
        stdout=out,
        stderr=err,
    )
    with patch("sys.stdin", io.StringIO("")):
        try:
            code = m.run()
        finally:
            m.close()
    return Result(out.getvalue(), code, _last_stderr_line(err.getvalue(), code))


def ensure_dspc_bundles() -> None:
    """Generate the compiler and stdlib artifacts used by portable runtimes.

    These are build artifacts; `make generate` rebuilds only what is stale.
    """
    proc = subprocess.run(
        ["make", "-s", "generate"], cwd=ROOT, capture_output=True, text=True, timeout=300
    )
    if proc.returncode != 0:
        pytest.fail(f"make generate failed:\n{proc.stdout}{proc.stderr}")


# Runtime exclusions reflect features outside each runtime's supported subset:
# diaspore-ts has no tasks or net; diaspore-lua additionally lacks host filesystem
# metadata and POSIX locale operations.
_TASKS_AND_NET = {
    "45_tasks",
    "70_threading_coordination",
    "71_threading_queue",
    "72_threading_limits",
    "73_socket_echo",
    "74_socket_limits",
    "106_thread_primitives",
    "109_socket_adapter",
}
TS_SKIP = set(_TASKS_AND_NET)
LUA_SKIP = _TASKS_AND_NET | {
    "89_filesystem_metadata",
    "98_posix_locale",
}


@pytest.fixture(scope="session")
def ts_vm() -> list[str]:
    """Command prefix for the TypeScript VM; skip if unavailable."""
    entry = TS_DIR / "src" / "cli.ts"
    ensure_dspc_bundles()
    if not entry.exists():
        pytest.skip("TypeScript VM source not present")
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js not installed")
    return [node, "--experimental-strip-types", "--no-warnings", str(entry)]


def run_diaspore_ts(entry: Path, ts_cmd: list[str], tmp_path: Path) -> Result:
    bundle = build(entry)
    bundle_path = tmp_path / "bundle.dsp.json"
    bundle_path.write_text(json.dumps(bundle), encoding="utf-8")
    cmd = [*ts_cmd, "run", str(bundle_path)]
    cmd += [f"--allow-{cap}" for cap in sorted(caps_for(entry))]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120, input="")
    return Result(proc.stdout, proc.returncode, _last_stderr_line(proc.stderr, proc.returncode))


@pytest.fixture(scope="session")
def lua_vm() -> list[str]:
    """Command prefix for the Lua VM; skip if unavailable."""
    entry = LUA_DIR / "cli.lua"
    ensure_dspc_bundles()
    if not entry.exists():
        pytest.skip("Lua VM source not present")
    lua = shutil.which("lua5.4") or shutil.which("lua")
    if lua is None:
        pytest.skip("Lua 5.4 not installed")
    return [lua, str(entry)]


def run_diaspore_lua(entry: Path, lua_cmd: list[str], tmp_path: Path) -> Result:
    bundle = build(entry)
    bundle_path = tmp_path / "bundle.dsp.json"
    bundle_path.write_text(json.dumps(bundle), encoding="utf-8")
    cmd = [*lua_cmd, "run", str(bundle_path)]
    cmd += [f"--allow-{cap}" for cap in sorted(caps_for(entry))]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120, input="")
    return Result(proc.stdout, proc.returncode, _last_stderr_line(proc.stderr, proc.returncode))


@pytest.fixture(scope="session")
def go_vm() -> Path:
    """Build (once) and return the Go VM binary; skip if unavailable."""
    if os.environ.get("DIASPORE_GO_BIN"):
        return Path(os.environ["DIASPORE_GO_BIN"])
    if not (GO_DIR / "go.mod").exists():
        pytest.skip("Go VM source not present")
    if shutil.which("go") is None:
        pytest.skip("Go toolchain not installed")
    ensure_dspc_bundles()
    GO_BIN.parent.mkdir(exist_ok=True)
    proc = subprocess.run(
        ["go", "build", "-o", str(GO_BIN), "./cmd/diaspore-go"],
        cwd=GO_DIR,
        capture_output=True,
        text=True,
        timeout=300,
    )
    if proc.returncode != 0:
        pytest.fail(f"go build failed:\n{proc.stderr}")
    return GO_BIN


def run_diaspore_go(entry: Path, go_bin: Path, tmp_path: Path) -> Result:
    bundle = build(entry)
    bundle_path = tmp_path / "bundle.dsp.json"
    bundle_path.write_text(json.dumps(bundle), encoding="utf-8")
    cmd = [str(go_bin), "run", str(bundle_path)]
    cmd += [f"--allow-{cap}" for cap in sorted(caps_for(entry))]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60, input="")
    return Result(proc.stdout, proc.returncode, _last_stderr_line(proc.stderr, proc.returncode))


def assert_matches(got: Result, expected: Result, label: str) -> None:
    assert got.stdout == expected.stdout, (
        f"{label}: stdout mismatch\n--- expected (CPython) ---\n{expected.stdout}"
        f"--- got ---\n{got.stdout}"
    )
    assert got.code == expected.code, (
        f"{label}: exit code {got.code} != {expected.code} (err: {got.err_line!r})"
    )
    if expected.code != 0:
        assert got.err_line == expected.err_line, (
            f"{label}: final stderr line {got.err_line!r} != {expected.err_line!r}"
        )


_expected_cache: dict[Path, Result] = {}


def expected_for(entry: Path) -> Result:
    """Use pinned output for Diaspore-only modules and CPython for other programs."""
    if entry not in _expected_cache:
        pinned = entry.with_suffix(".expected")
        if pinned.exists():
            _expected_cache[entry] = Result(pinned.read_text(encoding="utf-8"), 0, "")
        else:
            _expected_cache[entry] = run_cpython(entry)
    return _expected_cache[entry]
