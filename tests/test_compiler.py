"""Compiler unit tests: rejection quality, determinism, IR schema."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from diaspore.cli import main
from diaspore.compiler import DiasporeCompileError, compile_bundle, dumps
from diaspore.validator import BundleValidationError, validate_bundle


def compile_src(tmp_path: Path, source: str) -> dict:
    entry = tmp_path / "prog.py"
    entry.write_text(source, encoding="utf-8")
    return compile_bundle(str(entry))


class TestRejections:
    @pytest.mark.parametrize("source,fragment", [
        ("match x:\n    case Point(1, 2):\n        pass\n", "positional class patterns"),
        ("from definitely_not_a_pkg import *\n", "cannot resolve import"),
        ("from . import x\n", "no known parent package"),
        ("y = [v for v in (w := [1])]\n",
         "assignment expression cannot be used in a comprehension iterable"),
        ("def f(: int)\n", "syntax error"),
    ])
    def test_rejects_with_message(self, tmp_path: Path, source: str, fragment: str) -> None:
        with pytest.raises(DiasporeCompileError) as exc:
            compile_src(tmp_path, source)
        assert fragment in str(exc.value)

    def test_error_carries_location(self, tmp_path: Path) -> None:
        with pytest.raises(DiasporeCompileError) as exc:
            compile_src(tmp_path, "x = 1\ny = 2\nfrom . import z\n")
        assert exc.value.line == 3


class TestDeferredImports:
    def test_unresolvable_import_defers_to_runtime(self, tmp_path: Path) -> None:
        """CPython resolves imports at runtime: optional deps in
        try/except ImportError must compile and behave identically."""
        import io

        from diaspore import Machine
        entry = tmp_path / "prog.py"
        entry.write_text(
            "try:\n"
            "    import definitely_not_installed\n"
            "    print('have it')\n"
            "except ImportError as e:\n"
            "    print('optional dep missing:', e)\n"
            "def lazy():\n"
            "    import also_not_installed\n"
            "print('top-level survived')\n",
            encoding="utf-8")
        unresolved: dict = {}
        bundle = compile_bundle(str(entry), unresolved_out=unresolved)
        assert set(unresolved) == {"definitely_not_installed", "also_not_installed"}
        out = io.StringIO()
        assert Machine(bundle, stdout=out).run() == 0
        assert out.getvalue() == (
            "optional dep missing: No module named 'definitely_not_installed'\n"
            "top-level survived\n")

    def test_uncaught_unresolvable_import_fails_at_runtime(self, tmp_path: Path) -> None:
        import io

        from diaspore import Machine
        entry = tmp_path / "prog.py"
        entry.write_text("import nope_never\nprint('unreached')\n", encoding="utf-8")
        bundle = compile_bundle(str(entry))
        err = io.StringIO()
        assert Machine(bundle, stdout=io.StringIO(), stderr=err).run() == 1
        assert "ModuleNotFoundError: No module named 'nope_never'" in err.getvalue()


class TestOutput:
    def test_deterministic(self, tmp_path: Path) -> None:
        src = "def f(a, b=1):\n    return [x for x in range(a + b)]\nprint(f(2))\n"
        one = dumps(compile_src(tmp_path, src))
        two = dumps(compile_src(tmp_path, src))
        assert one == two

    def test_bundle_validates(self, tmp_path: Path) -> None:
        bundle = compile_src(tmp_path, (
            "class C:\n"
            "    def m(self, *a, k=1, **kw):\n"
            "        yield from a\n"
            "with_stmt = [w for w in 'ab' if w]\n"
            "f = lambda: {**{}, 'x': 1}\n"
        ))
        validate_bundle(bundle)

    def test_roundtrips_through_json(self, tmp_path: Path) -> None:
        bundle = compile_src(tmp_path, "print('hi')\n")
        assert json.loads(dumps(bundle)) == bundle
        assert json.loads(dumps(bundle, pretty=True)) == bundle

    def test_multi_module_bundle(self, tmp_path: Path) -> None:
        (tmp_path / "helper.py").write_text("HELPER = 1\n", encoding="utf-8")
        (tmp_path / "prog.py").write_text("import helper\nprint(helper.HELPER)\n",
                                          encoding="utf-8")
        bundle = compile_bundle(str(tmp_path / "prog.py"))
        assert set(bundle["modules"]) == {"prog", "helper"}
        assert bundle["main"] == "prog"
        validate_bundle(bundle)


@pytest.mark.parametrize("command", ["build", "check", "run"])
def test_cli_module_search_paths(tmp_path: Path, capsys, command: str) -> None:
    source = tmp_path / "source"
    source.mkdir()
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "left.py").write_text("VALUE = 12\n", encoding="utf-8")
    (second / "right.py").write_text("VALUE = 30\n", encoding="utf-8")
    entry = source / "app.py"
    entry.write_text("import left\nimport right\nprint(left.VALUE + right.VALUE)\n",
                     encoding="utf-8")
    output = tmp_path / "app.dsp.json"
    args = [command, str(entry), "--path", str(first), "--path", str(second)]
    if command == "build":
        args.extend(["-o", str(output)])
    assert main(args) == 0
    captured = capsys.readouterr()
    if command == "run":
        assert captured.out == "42\n"
    elif command == "check":
        assert "warning" not in captured.err
        assert "app, left, right" in captured.err
    else:
        assert set(json.loads(output.read_text())["modules"]) == {"app", "left", "right"}


HOSTILE_BUNDLES = [
    {},
    {"diaspore": 2, "main": "m", "modules": {}},
    {"diaspore": 1, "main": "m", "modules": {}},
    {"diaspore": 1, "main": "m",
     "modules": {"m": {"name": "m", "file": "m.py",
                       "body": [{"op": "nonsense"}]}}},
    {"diaspore": 1, "main": "m",
     "modules": {"m": {"name": "m", "file": "m.py",
                       "body": [{"op": "assign", "targets": [], "v": {"op": "name", "id": "x"}}]}}},
    {"diaspore": 1, "main": "m",
     "modules": {"m": {"name": "m", "file": "m.py",
                       "body": [{"op": "pass", "ln": 1, "extra_field": True}]}}},
    {"diaspore": 1, "main": "m",
     "modules": {"m": {"name": "m", "file": "m.py",
                       "body": [{"op": "boolop", "kind": "and",
                                 "values": [{"op": "name", "id": "x"}]}]}}},
    {"diaspore": 1, "main": "m",
     "modules": {"m": {"name": "m", "file": "m.py",
                       "body": [{"op": "binop", "kind": "hax",
                                 "l": {"op": "name", "id": "x"},
                                 "r": {"op": "name", "id": "y"}}]}}},
    {"diaspore": 1, "main": "m",
     "modules": {"m": {"name": "m", "file": "m.py",
                       "body": [{"op": "return", "v": 42}]}}},
]


class TestValidator:
    @pytest.mark.parametrize("bad", HOSTILE_BUNDLES)
    def test_rejects_malformed_bundles(self, bad: dict) -> None:
        with pytest.raises(BundleValidationError):
            validate_bundle(bad)

    def test_accepts_compiled_bundles(self, tmp_path: Path) -> None:
        bundle = compile_src(tmp_path, (
            "class C:\n"
            "    def m(self, *a, k=1, **kw):\n"
            "        yield from a\n"
            "match [1]:\n"
            "    case [x]:\n"
            "        pass\n"
            "f = lambda: {**{}, 'x': 1}\n"
        ))
        validate_bundle(bundle)


class TestSchemaCrossCheck:
    """The stdlib validator and the pydantic models (dev-only) must agree."""

    pydantic = pytest.importorskip("pydantic")

    def test_schema_exports(self) -> None:
        from diaspore.ir import bundle_schema
        schema = bundle_schema()
        assert "$defs" in schema and "properties" in schema

    @pytest.mark.parametrize("bad", HOSTILE_BUNDLES)
    def test_hostile_rejected_by_both(self, bad: dict) -> None:
        from diaspore.ir import validate_bundle as pydantic_validate
        with pytest.raises(BundleValidationError):
            validate_bundle(bad)
        with pytest.raises(self.pydantic.ValidationError):
            pydantic_validate(bad)

    def test_corpus_accepted_by_both(self) -> None:
        from diaspore.ir import validate_bundle as pydantic_validate

        from .conftest import corpus_entries
        for entry in corpus_entries():
            bundle = compile_bundle(str(entry))
            validate_bundle(bundle)
            pydantic_validate(bundle)
