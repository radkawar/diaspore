"""Compare random deterministic programs against CPython.

Scale with DIASPORE_FUZZ_SEEDS (default 25 for CI; `make fuzz` runs 300).
A mismatch saves the offending program next to the failure message.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from .conftest import (
    assert_matches,
    run_cpython,
    run_diaspore_go,
    run_diaspore_lua,
    run_diaspore_py,
    run_diaspore_ts,
)
from .fuzzgen import generate

SEEDS = int(os.environ.get("DIASPORE_FUZZ_SEEDS", "25"))
FAILURE_DIR = Path(__file__).parent / "fuzz_failures"


def _entry(tmp_path: Path, seed: int) -> Path:
    entry = tmp_path / f"fuzz_{seed}.py"
    entry.write_text(generate(seed), encoding="utf-8")
    return entry


def _report(entry: Path, seed: int, exc: AssertionError) -> None:
    FAILURE_DIR.mkdir(exist_ok=True)
    saved = FAILURE_DIR / f"seed_{seed}.py"
    saved.write_text(entry.read_text(encoding="utf-8"), encoding="utf-8")
    raise AssertionError(f"fuzz seed {seed} diverged (program saved to {saved}):\n{exc}")


@pytest.mark.parametrize("seed", range(SEEDS))
def test_fuzz_python_vm(seed: int, tmp_path: Path) -> None:
    entry = _entry(tmp_path, seed)
    try:
        # Bound execution so a generated infinite loop cannot stall the suite.
        assert_matches(run_diaspore_py(entry, max_steps=5_000_000),
                       run_cpython(entry), "diaspore-py")
    except AssertionError as exc:
        _report(entry, seed, exc)


@pytest.mark.parametrize("seed", range(SEEDS))
def test_fuzz_go_vm(seed: int, go_vm: Path, tmp_path: Path) -> None:
    entry = _entry(tmp_path, seed)
    try:
        assert_matches(run_diaspore_go(entry, go_vm, tmp_path), run_cpython(entry), "diaspore-go")
    except AssertionError as exc:
        _report(entry, seed, exc)


@pytest.mark.parametrize("seed", range(SEEDS))
def test_fuzz_ts_vm(seed: int, ts_vm: list[str], tmp_path: Path) -> None:
    entry = _entry(tmp_path, seed)
    try:
        assert_matches(run_diaspore_ts(entry, ts_vm, tmp_path), run_cpython(entry), "diaspore-ts")
    except AssertionError as exc:
        _report(entry, seed, exc)


@pytest.mark.parametrize("seed", range(SEEDS))
def test_fuzz_lua_vm(seed: int, lua_vm: list[str], tmp_path: Path) -> None:
    entry = _entry(tmp_path, seed)
    try:
        assert_matches(
            run_diaspore_lua(entry, lua_vm, tmp_path), run_cpython(entry), "diaspore-lua",
        )
    except AssertionError as exc:
        _report(entry, seed, exc)
