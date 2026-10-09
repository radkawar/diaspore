"""Compare the conformance corpus across CPython and all Diaspore runtimes."""

from __future__ import annotations

from pathlib import Path

import pytest

from .conftest import (
    LUA_SKIP,
    TS_SKIP,
    assert_matches,
    corpus_entries,
    expected_for,
    run_diaspore_go,
    run_diaspore_lua,
    run_diaspore_py,
    run_diaspore_ts,
)

ENTRIES = corpus_entries()


def _id(p: Path) -> str:
    return p.parent.name if p.name == "main.py" else p.stem


@pytest.mark.parametrize("entry", ENTRIES, ids=_id)
def test_python_vm(entry: Path) -> None:
    assert_matches(run_diaspore_py(entry), expected_for(entry), "diaspore-py")


@pytest.mark.parametrize("entry", ENTRIES, ids=_id)
def test_go_vm(entry: Path, go_vm: Path, tmp_path: Path) -> None:
    assert_matches(run_diaspore_go(entry, go_vm, tmp_path), expected_for(entry), "diaspore-go")


@pytest.mark.parametrize("entry", ENTRIES, ids=_id)
def test_ts_vm(entry: Path, ts_vm: list[str], tmp_path: Path) -> None:
    if _id(entry) in TS_SKIP:
        pytest.skip(f"documented diaspore-ts skip: {_id(entry)}")
    assert_matches(run_diaspore_ts(entry, ts_vm, tmp_path), expected_for(entry), "diaspore-ts")


@pytest.mark.parametrize("entry", ENTRIES, ids=_id)
def test_lua_vm(entry: Path, lua_vm: list[str], tmp_path: Path) -> None:
    if _id(entry) in LUA_SKIP:
        pytest.skip(f"documented diaspore-lua skip: {_id(entry)}")
    assert_matches(run_diaspore_lua(entry, lua_vm, tmp_path), expected_for(entry), "diaspore-lua")
