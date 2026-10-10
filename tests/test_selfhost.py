"""Compare native and self-hosted compiler output.

Both compilers must emit identical bundles for the corpus and for dspc itself.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from diaspore import Machine, compile_bundle

from .conftest import ROOT, corpus_entries

SELFHOST = ROOT / "selfhost" / "dspc.py"


@pytest.fixture(scope="session")
def dspc_bundle() -> dict:
    if not SELFHOST.exists():
        pytest.skip("self-hosted compiler not present")
    return compile_bundle(str(SELFHOST))


def selfhost_compile(
    dspc: dict, entry: Path, search_paths=(), *, self_contained=False, stdlib=True
) -> dict:
    out, err = io.StringIO(), io.StringIO()
    m = Machine(
        dspc,
        argv=[
            "dspc",
            *[arg for path in search_paths for arg in ("--path", str(path))],
            *(["--self-contained"] if self_contained else []),
            *([] if stdlib else ["--no-stdlib"]),
            str(entry),
        ],
        caps={"fs"},
        policy={"fs": {"read": [str(ROOT), "/tmp"], "write": []}},
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
                return (
                    f"module {name!r} stmt #{i} differs:\n"
                    f"  native:   {json.dumps(sa)[:400]}\n"
                    f"  selfhost: {json.dumps(sb)[:400]}"
                )
        if len(a["body"]) != len(b["body"]):
            return (
                f"module {name!r} stmt count differs: "
                f"native {len(a['body'])} vs selfhost {len(b['body'])}"
            )
        return f"module {name!r} differs outside body"
    return "top-level fields differ"


def _id(p: Path) -> str:
    return p.parent.name if p.name == "main.py" else p.stem


@pytest.mark.parametrize("entry", corpus_entries(), ids=_id)
def test_matches_native_compiler(dspc_bundle: dict, entry: Path) -> None:
    native = compile_bundle(str(entry))
    got = selfhost_compile(dspc_bundle, entry)
    assert got == native, f"selfhost/native divergence on {entry.name}: {_diff_hint(native, got)}"


def test_surrogate_literal_transport(dspc_bundle: dict, tmp_path: Path) -> None:
    """Native and interpreted emitters retain explicit Python codepoints."""
    entry = tmp_path / "literal.py"
    values = [
        "ASCII",
        "\U000103ff",
        "\ud800",
        "\udfff",
        "\ud800\udfff",
        "\udfff\ud800",
        "\U000103ff\ud800\udfff",
    ]
    entry.write_text(
        "".join(f"value = {ascii(value)}\ntext = f{ascii(value + '{1}')}\n" for value in values),
        encoding="utf-8",
    )
    native = compile_bundle(str(entry))
    assert selfhost_compile(dspc_bundle, entry) == native


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


@pytest.mark.parametrize("self_contained", [False, True])
def test_extra_import_roots(dspc_bundle: dict, tmp_path: Path, self_contained: bool) -> None:
    """Search local files before extra roots and preserve external filenames."""
    app = tmp_path / "app"
    packages = tmp_path / "site-packages"
    app.mkdir()
    (packages / "example").mkdir(parents=True)
    entry = app / "main.py"
    entry.write_text("from example import result\nimport local\nprint(result, local.value)\n")
    (app / "local.py").write_text("value = 1\n")
    (packages / "local.py").write_text("value = 2\n")
    (packages / "example" / "__init__.py").write_text("from .helper import result\n")
    (packages / "example" / "helper.py").write_text(
        "from itertools import chain\nresult = list(chain([1], [2]))\n"
    )
    paths = [str(packages)]
    native = compile_bundle(str(entry), search_paths=paths, self_contained=self_contained)
    got = selfhost_compile(dspc_bundle, entry, paths, self_contained=self_contained)
    assert got == native, _diff_hint(native, got)
    if self_contained:
        assert {"example", "example.helper", "itertools", "local"} <= got["modules"].keys()
        assert got["modules"]["itertools"]["file"] == "<stdlib>/itertools.py"
        assert "stdlib" not in got
    else:
        assert set(got["modules"]) == {"main", "example", "example.helper", "local"}
        assert got["stdlib"].startswith("sha256:")
    assert got["modules"]["local"]["file"] == str(app / "local.py")


@pytest.mark.parametrize("self_contained", [False, True])
def test_shipped_import_roots(dspc_bundle: dict, tmp_path: Path, self_contained: bool) -> None:
    """Resolve maintained and upstream modules with stable bundle filenames."""
    entry = tmp_path / "entry.py"
    entry.write_text("import itertools\nimport keyword\n", encoding="utf-8")
    native = compile_bundle(str(entry), self_contained=self_contained)
    got = selfhost_compile(dspc_bundle, entry, self_contained=self_contained)
    assert got == native, _diff_hint(native, got)
    if self_contained:
        assert got["modules"]["itertools"]["file"] == "<stdlib>/itertools.py"
        assert got["modules"]["keyword"]["file"] == "<stdlib>/keyword.py"
        assert "stdlib" not in got
    else:
        assert set(got["modules"]) == {"entry"}
        assert got["stdlib"].startswith("sha256:")


@pytest.mark.parametrize("self_contained", [False, True])
def test_program_module_shadows_store(
    dspc_bundle: dict, tmp_path: Path, self_contained: bool
) -> None:
    entry = tmp_path / "prog.py"
    entry.write_text("import keyword\nprint(keyword.MARKER)\n", encoding="utf-8")
    module = tmp_path / "keyword.py"
    module.write_text("MARKER = 'program module'\n", encoding="utf-8")
    native = compile_bundle(str(entry), self_contained=self_contained)
    got = selfhost_compile(dspc_bundle, entry, self_contained=self_contained)
    assert got == native, _diff_hint(native, got)
    assert set(got["modules"]) == {"prog", "keyword"}
    assert got["modules"]["keyword"]["file"] == str(module)
    assert ("stdlib" not in got) == self_contained


def test_no_stdlib_preserves_program_imports(dspc_bundle: dict, tmp_path: Path) -> None:
    app = tmp_path / "app"
    packages = tmp_path / "packages"
    app.mkdir()
    packages.mkdir()
    entry = app / "prog.py"
    entry.write_text(
        "import helper\nimport itertools\nvalue = data.decode('utf-16')\n", encoding="utf-8"
    )
    helper = packages / "helper.py"
    helper.write_text("MARKER = True\n", encoding="utf-8")
    paths = [str(packages)]
    native = compile_bundle(str(entry), search_paths=paths, stdlib=False)
    got = selfhost_compile(dspc_bundle, entry, paths, stdlib=False)
    assert got == native, _diff_hint(native, got)
    assert "stdlib" not in got
    assert set(got["modules"]) == {"prog", "helper"}
    assert got["modules"]["helper"]["file"] == str(helper)


@pytest.mark.parametrize(
    "source",
    [
        "value = data.decode('utf-16')",
        "value = compile(source, filename, 'exec', flags=1024)",
    ],
)
def test_linked_provider_bundle_matches_native(
    dspc_bundle: dict, tmp_path: Path, source: str
) -> None:
    for name in ("codecs", "_ast"):
        (tmp_path / f"{name}.py").write_text("MARKER = True\n", encoding="utf-8")
    entry = tmp_path / "prog.py"
    entry.write_text(source + "\n", encoding="utf-8")
    native = compile_bundle(str(entry))
    got = selfhost_compile(dspc_bundle, entry)
    assert got == native, _diff_hint(native, got)
    assert set(got["modules"]) == {"prog"}
    assert got["stdlib"].startswith("sha256:")


@pytest.mark.parametrize(
    "source,modules",
    [
        ("print('hello')\n", {"prog"}),
        ("value = data.decode('UTF-8', 'ignore')\n", {"prog"}),
        ("value = str(data, encoding='ISO_8859-1')\n", {"prog"}),
        (
            "value = data.decode('utf-16')\n",
            {
                "prog",
                "codecs",
                "encodings",
                "encodings.aliases",
                "encodings.mbcs",
                "encodings.utf_16",
            },
        ),
        ("value = data.encode(errors=handler)\n", {"prog", "codecs"}),
        ("value = bytes(data, encoding=encoding)\n", {"prog", "codecs"}),
        (
            "value = open(path, encoding='utf-16')\n",
            {
                "prog",
                "codecs",
                "encodings",
                "encodings.aliases",
                "encodings.mbcs",
                "encodings.utf_16",
            },
        ),
        ("value = data.decode(**options)\n", {"prog", "codecs"}),
        ("value = compile(source, filename, 'exec', 0)\n", {"prog"}),
        ("value = compile(source, filename, 'exec', flags=1024)\n", {"prog", "_ast"}),
        ("value = compile(source, filename, 'exec', flags=flags)\n", {"prog", "_ast"}),
        (
            "import builtins\nvalue = builtins.compile(source, filename, 'exec', flags=1024)\n",
            {"prog", "_ast"},
        ),
        ("import re\nvalue = re.compile(pattern, flags)\n", {"prog", "re"}),
    ],
)
def test_selective_provider_bundle_matches_native(
    dspc_bundle: dict, tmp_path: Path, source: str, modules: set[str]
) -> None:
    for name in ("codecs", "_ast", "re"):
        (tmp_path / f"{name}.py").write_text("MARKER = True\n", encoding="utf-8")
    entry = tmp_path / "prog.py"
    entry.write_text(source, encoding="utf-8")
    native = compile_bundle(str(entry), self_contained=True)
    got = selfhost_compile(dspc_bundle, entry, self_contained=True)
    assert set(native["modules"]) == modules
    assert got == native, _diff_hint(native, got)
