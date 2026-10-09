"""Compare native and self-hosted compiler output.

Both compilers must emit identical bundles for the corpus and for dspc itself.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from diaspore import Machine, compile_bundle
from diaspore.compiler import STDLIB_DIR

from .conftest import ROOT, corpus_entries

SELFHOST = ROOT / "selfhost" / "dspc.py"


@pytest.fixture(scope="session")
def dspc_bundle() -> dict:
    if not SELFHOST.exists():
        pytest.skip("self-hosted compiler not present")
    return compile_bundle(str(SELFHOST))


def selfhost_compile(dspc: dict, entry: Path, search_paths=()) -> dict:
    out, err = io.StringIO(), io.StringIO()
    m = Machine(
        dspc,
        argv=["dspc", *[arg for path in search_paths for arg in ("--path", str(path))],
              "--stdlib", STDLIB_DIR, str(entry)],
        caps={"fs"},
        policy={"fs": {"read": [str(ROOT), STDLIB_DIR, "/tmp"], "write": []}},
        max_depth=3000,
        stdout=out,
        stderr=err,
    )
    code = m.run()
    assert code == 0, f"selfhost compiler failed on {entry}:\n{err.getvalue()}"
    return json.loads(out.getvalue())


def _diff_hint(native: dict, selfhosted: dict) -> str:
    for name in sorted(set(native["modules"]) | set(selfhosted["modules"])):
        a = native["modules"].get(name)
        b = selfhosted["modules"].get(name)
        if a == b:
            continue
        if a is None or b is None:
            return f"module {name!r} missing on one side"
        for i, (sa, sb) in enumerate(zip(a["body"], b["body"])):
            if sa != sb:
                return (f"module {name!r} stmt #{i} differs:\n"
                        f"  native:   {json.dumps(sa)[:400]}\n"
                        f"  selfhost: {json.dumps(sb)[:400]}")
        if len(a["body"]) != len(b["body"]):
            return (f"module {name!r} stmt count differs: "
                    f"native {len(a['body'])} vs selfhost {len(b['body'])}")
        return f"module {name!r} differs outside body"
    return "top-level fields differ"


def _id(p: Path) -> str:
    return p.parent.name if p.name == "main.py" else p.stem


@pytest.mark.parametrize("entry", corpus_entries(), ids=_id)
def test_matches_native_compiler(dspc_bundle: dict, entry: Path) -> None:
    native = compile_bundle(str(entry))
    got = selfhost_compile(dspc_bundle, entry)
    assert got == native, f"selfhost/native divergence on {entry.name}: " \
                          f"{_diff_hint(native, got)}"


def test_bootstrap_fixpoint(dspc_bundle: dict) -> None:
    """Compile dspc with itself and compare against the native compiler."""
    native = compile_bundle(str(SELFHOST))
    got = selfhost_compile(dspc_bundle, SELFHOST)
    assert got == native, f"fixpoint divergence: {_diff_hint(native, got)}"


def test_second_order_fixpoint(dspc_bundle: dict, tmp_path: Path) -> None:
    """Check that recompiling with the self-hosted output reaches a fixed point."""
    stage2 = selfhost_compile(dspc_bundle, SELFHOST)
    got = selfhost_compile(stage2, SELFHOST)
    assert got == stage2


def test_extra_import_roots(dspc_bundle: dict, tmp_path: Path) -> None:
    """Search local files before extra roots and preserve external filenames."""
    app = tmp_path / "app"
    packages = tmp_path / "site-packages"
    app.mkdir()
    (packages / "example").mkdir(parents=True)
    entry = app / "main.py"
    entry.write_text("from example import result\nimport local\nprint(result, local.value)\n")
    (app / "local.py").write_text("value = 1\n")
    (packages / "local.py").write_text("value = 2\n")
    (packages / "example" / "__init__.py").write_text(
        "from .helper import result\n")
    (packages / "example" / "helper.py").write_text(
        "from itertools import chain\nresult = list(chain([1], [2]))\n")
    paths = [str(packages)]
    native = compile_bundle(str(entry), search_paths=paths)
    got = selfhost_compile(dspc_bundle, entry, paths)
    assert got == native, _diff_hint(native, got)
    assert {"example", "example.helper", "itertools", "local"} <= got["modules"].keys()
    assert got["modules"]["local"]["file"] == str(app / "local.py")
    assert got["modules"]["itertools"]["file"] == "<stdlib>/itertools.py"
