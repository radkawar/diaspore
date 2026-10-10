"""Compiler unit tests: rejection quality, determinism, IR schema."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from diaspore.cli import main
from diaspore.compiler import DiasporeCompileError, compile_bundle, dumps
from diaspore.validator import BundleValidationError, validate_bundle


def compile_src(tmp_path: Path, source: str, *, self_contained=False) -> dict:
    entry = tmp_path / "prog.py"
    entry.write_text(source, encoding="utf-8")
    return compile_bundle(str(entry), self_contained=self_contained)


@pytest.mark.parametrize(
    "value",
    [
        "ASCII",
        "\U000103ff",
        "\ud800\udfff",
        "\ud800x\udfff",
        "\U000103ff\ud800\udfff",
    ],
)
def test_string_ast_source_parity(tmp_path: Path, value: str) -> None:
    import ast

    from diaspore.compiler import ModuleCompiler

    source = f"value = {ascii(value)}\ntext = f{ascii(value + '{1}')}\n"
    bundle = compile_src(tmp_path, source)
    compiler = ModuleCompiler("prog", str(tmp_path / "prog.py"), source)
    constant = compiler.expr(ast.Constant(value=value))
    formatted = compiler.expr(
        ast.JoinedStr(
            values=[
                ast.Constant(value=value),
                ast.FormattedValue(value=ast.Constant(value=1), conversion=-1),
            ]
        )
    )
    assert bundle["modules"]["prog"]["body"][0]["v"] == constant
    emitted = bundle["modules"]["prog"]["body"][1]["v"]
    assert emitted["parts"][0] == formatted["parts"][0]
    validate_bundle(bundle)


def _invalid_string_codepoints_bundle(tmp_path: Path, points, fstring: bool) -> dict:
    bundle = compile_src(tmp_path, "value = 'text'\n")
    node = bundle["modules"]["prog"]["body"][0]["v"]
    if fstring:
        node.clear()
        node.update({"op": "fstring", "parts": [{"s": "text", "codepoints": points}]})
    else:
        node["codepoints"] = points
    return bundle


@pytest.mark.parametrize("points", [[-1], [0x110000], [True], [1.5], ["55296"], None])
@pytest.mark.parametrize("fstring", [False, True])
def test_invalid_string_codepoints(tmp_path: Path, points, fstring: bool) -> None:
    bundle = _invalid_string_codepoints_bundle(tmp_path, points, fstring)
    with pytest.raises(BundleValidationError):
        validate_bundle(bundle)


@pytest.mark.parametrize("points", [[-1], [0x110000], [True], [1.5], ["55296"], None])
@pytest.mark.parametrize("fstring", [False, True])
def test_invalid_string_codepoints_typed(tmp_path: Path, points, fstring: bool) -> None:
    pydantic = pytest.importorskip("pydantic")
    from diaspore.ir import validate_bundle as typed_validate

    bundle = _invalid_string_codepoints_bundle(tmp_path, points, fstring)
    with pytest.raises(pydantic.ValidationError):
        typed_validate(bundle)


class TestRejections:
    @pytest.mark.parametrize(
        "source,fragment",
        [
            ("from . import x\n", "no known parent package"),
            (
                "y = [v for v in (w := [1])]\n",
                "assignment expression cannot be used in a comprehension iterable",
            ),
            ("def f(: int)\n", "syntax error"),
        ],
    )
    def test_rejects_with_message(self, tmp_path: Path, source: str, fragment: str) -> None:
        with pytest.raises(DiasporeCompileError) as exc:
            compile_src(tmp_path, source)
        assert fragment in str(exc.value)

    def test_error_carries_location(self, tmp_path: Path) -> None:
        with pytest.raises(DiasporeCompileError) as exc:
            compile_src(tmp_path, "x = 1\ny = 2\nfrom . import z\n")
        assert exc.value.line == 3


class TestDeferredImports:
    @pytest.mark.parametrize(
        "statement",
        [
            "import definitely_not_installed",
            "from definitely_not_installed import *",
        ],
    )
    def test_unresolvable_import_defers_to_runtime(self, tmp_path: Path, statement: str) -> None:
        """CPython resolves imports at runtime: optional deps in
        try/except ImportError must compile and behave identically."""
        import io

        from diaspore import Machine

        entry = tmp_path / "prog.py"
        entry.write_text(
            "try:\n"
            f"    {statement}\n"
            "    print('have it')\n"
            "except ImportError as e:\n"
            "    print('optional dep missing:', e)\n"
            "def lazy():\n"
            "    import also_not_installed\n"
            "print('top-level survived')\n",
            encoding="utf-8",
        )
        unresolved: dict = {}
        bundle = compile_bundle(str(entry), unresolved_out=unresolved)
        assert set(unresolved) == {"definitely_not_installed", "also_not_installed"}
        out = io.StringIO()
        assert Machine(bundle, stdout=out).run() == 0
        assert out.getvalue() == (
            "optional dep missing: No module named 'definitely_not_installed'\ntop-level survived\n"
        )

    @pytest.mark.parametrize(
        "statement",
        [
            "import nope_never",
            "from nope_never import *",
        ],
    )
    def test_uncaught_unresolvable_import_fails_at_runtime(
        self, tmp_path: Path, statement: str
    ) -> None:
        import io

        from diaspore import Machine

        entry = tmp_path / "prog.py"
        entry.write_text(f"{statement}\nprint('unreached')\n", encoding="utf-8")
        bundle = compile_bundle(str(entry))
        err = io.StringIO()
        assert Machine(bundle, stdout=io.StringIO(), stderr=err).run() == 1
        assert "ModuleNotFoundError: No module named 'nope_never'" in err.getvalue()

    @pytest.mark.parametrize("module", ["unresolved_accelerator", "math", "exports"])
    def test_star_import_keeps_runtime_marker(self, tmp_path: Path, module: str) -> None:
        (tmp_path / "exports.py").write_text(
            "__all__ = ['one']\n__all__.append('_two')\none = 1\n_two = 2\n", encoding="utf-8"
        )
        bundle = compile_src(tmp_path, f"from {module} import *\n")
        node = bundle["modules"]["prog"]["body"][0]
        assert node == {
            "op": "importfrom",
            "module": module,
            "names": [{"n": "*", "alias": "*"}],
            "ln": 1,
        }
        validate_bundle(bundle)


class TestImplicitProviders:
    @pytest.mark.parametrize(
        "source,providers",
        [
            ("print('hello')", ()),
            ("data.encode()", ()),
            ("data.decode('UTF-8', 'surrogateescape')", ()),
            ("data.encode('US ASCII', errors='backslashreplace')", ()),
            ("data.decode('iso_8859-1', 'replace')", ()),
            ("str(data, encoding='latin1', errors='ignore')", ()),
            ("bytes(data, 'cp65001')", ()),
            ("open(path)", ()),
            ("open(path, encoding='utf8', errors='strict')", ()),
            (
                "data.decode('utf-16')",
                ("codecs", "encodings", "encodings.aliases", "encodings.mbcs", "encodings.utf_16"),
            ),
            ("data.encode('utf-8', 'surrogatepass')", ("codecs",)),
            ("data.encode('u-t-f-8')", ("codecs",)),
            ("data.decode(encoding)", ("codecs",)),
            ("data.encode(errors=handler)", ("codecs",)),
            ("decoder.decode(data)", ("codecs",)),
            ("str(data, encoding=encoding)", ("codecs",)),
            ("bytes(data, 'utf-8', handler)", ("codecs",)),
            (
                "builtins.str(data, encoding='utf-16')",
                ("codecs", "encodings", "encodings.aliases", "encodings.mbcs", "encodings.utf_16"),
            ),
            (
                "open(path, 'r', -1, 'utf-16')",
                ("codecs", "encodings", "encodings.aliases", "encodings.mbcs", "encodings.utf_16"),
            ),
            ("open(path, errors='custom')", ("codecs",)),
            ("data.decode(*arguments)", ("codecs",)),
            ("str(data, **options)", ("codecs",)),
            ("compile(source, filename, 'exec')", ()),
            ("compile(source, filename, 'exec', 0)", ()),
            ("compile(source, filename, 'exec', flags=512)", ()),
            ("compile(source, filename, 'exec', +1024)", ("_ast",)),
            ("compile(source, filename, 'exec', flags=1536)", ("_ast",)),
            ("compile(source, filename, 'exec', flags=flags)", ("_ast",)),
            ("builtins.compile(source, filename, 'exec', flags=1024)", ("_ast",)),
            ("compile(source, filename, 'exec', **options)", ("_ast",)),
            ("compile(*arguments)", ("_ast",)),
            ("compile(source, filename, 'exec', 0, *remaining)", ()),
            ("re.compile(pattern, flags)", ()),
        ],
    )
    def test_selective_dependencies(self, tmp_path: Path, source: str, providers) -> None:
        for name in ("codecs", "_ast"):
            (tmp_path / f"{name}.py").write_text("MARKER = True\n", encoding="utf-8")
        bundle = compile_src(tmp_path, source + "\n", self_contained=True)
        assert set(bundle["modules"]) == {"prog", *providers}

    @pytest.mark.parametrize(
        "encoding",
        [
            "utf8",
            "utf-8",
            "UTF_8",
            "u8",
            "utf",
            "cp65001",
            "ascii",
            "US-ASCII",
            "us_ascii",
            "646",
            "latin-1",
            "latin1",
            "latin",
            "ISO8859-1",
            "iso-8859-1",
            "iso_8859_1_1987",
            "cp819",
            "l1",
            "ibm819",
            "8859",
        ],
    )
    def test_native_encoding_aliases_need_no_provider(self, tmp_path: Path, encoding: str) -> None:
        bundle = compile_src(tmp_path, f"value = data.decode({encoding!r})\n", self_contained=True)
        assert set(bundle["modules"]) == {"prog"}

    @pytest.mark.parametrize(
        "source",
        [
            "value = data.decode('utf-16')",
            "value = data.encode(errors=handler)",
            "value = compile(source, filename, 'exec', flags=1024)",
        ],
    )
    def test_linked_skips_implicit_providers(self, tmp_path: Path, source: str) -> None:
        for name in ("codecs", "_ast"):
            (tmp_path / f"{name}.py").write_text("MARKER = True\n", encoding="utf-8")
        bundle = compile_src(tmp_path, source + "\n")
        assert set(bundle["modules"]) == {"prog"}
        assert bundle["stdlib"].startswith("sha256:")

    @pytest.mark.parametrize("module", ["codecs", "_ast", "itertools", "keyword"])
    @pytest.mark.parametrize("self_contained", [False, True])
    def test_explicit_import_prefers_program_module(
        self, tmp_path: Path, module: str, self_contained: bool
    ) -> None:
        path = tmp_path / f"{module}.py"
        path.write_text("MARKER = True\n", encoding="utf-8")
        bundle = compile_src(tmp_path, f"import {module}\n", self_contained=self_contained)
        assert set(bundle["modules"]) == {"prog", module}
        assert bundle["modules"][module]["file"] == str(path)
        assert ("stdlib" not in bundle) == self_contained


class TestClassPatterns:
    def test_positional_class_patterns_use_match_args(self, tmp_path: Path) -> None:
        import io

        from diaspore import Machine

        bundle = compile_src(
            tmp_path,
            (
                "class Point:\n"
                "    __match_args__ = ('x', 'y')\n"
                "    def __init__(self, x, y):\n"
                "        self.x = x\n"
                "        self.y = y\n"
                "match Point(1, 2):\n"
                "    case Point(a, b):\n"
                "        print(a, b)\n"
            ),
        )
        validate_bundle(bundle)
        out = io.StringIO()
        assert Machine(bundle, stdout=out).run() == 0
        assert out.getvalue() == "1 2\n"


class TestParameters:
    def test_positional_only_count_is_preserved(self, tmp_path: Path) -> None:
        bundle = compile_src(
            tmp_path,
            (
                "def ordinary(a):\n"
                "    return a\n"
                "def mixed(a, b, /, c=3, *rest, d=4, **kwargs):\n"
                "    return a, b, c, rest, d, kwargs\n"
                "single = lambda value, /, other=2: value + other\n"
            ),
        )
        ordinary, mixed, single = bundle["modules"]["prog"]["body"]
        assert "posonly" not in ordinary["params"]
        assert mixed["params"]["args"] == ["a", "b", "c"]
        assert mixed["params"]["posonly"] == 2
        assert single["v"]["params"]["args"] == ["value", "other"]
        assert single["v"]["params"]["posonly"] == 1
        validate_bundle(bundle)


class TestFunctionKinds:
    def test_async_generator_preserves_source_yield(self, tmp_path: Path) -> None:
        bundle = compile_src(
            tmp_path,
            (
                "async def coroutine():\n"
                "    await pending()\n"
                "async def asynchronous_generator():\n"
                "    yield await pending()\n"
                "def synchronous_generator():\n"
                "    yield 1\n"
                "async def nested_scopes():\n"
                "    def inner():\n"
                "        yield 2\n"
                "    async def inner_async():\n"
                "        yield 3\n"
                "    class Inner:\n"
                "        def method(self):\n"
                "            yield 4\n"
                "    expression = (value for value in [])\n"
                "    return 5\n"
                "async def default_yield():\n"
                "    def inner(value=(yield 6)):\n"
                "        return value\n"
            ),
        )
        nodes = {node["name"]: node for node in bundle["modules"]["prog"]["body"]}
        assert nodes["coroutine"]["gen"] and nodes["coroutine"]["coro"]
        assert "asyncgen" not in nodes["coroutine"]
        assert nodes["asynchronous_generator"]["asyncgen"] is True
        assert nodes["synchronous_generator"]["gen"] is True
        assert "asyncgen" not in nodes["synchronous_generator"]
        assert "asyncgen" not in nodes["nested_scopes"]
        assert nodes["default_yield"]["asyncgen"] is True
        validate_bundle(bundle)


class TestOutput:
    def test_deterministic(self, tmp_path: Path) -> None:
        src = "def f(a, b=1):\n    return [x for x in range(a + b)]\nprint(f(2))\n"
        one = dumps(compile_src(tmp_path, src))
        two = dumps(compile_src(tmp_path, src))
        assert one == two

    def test_bundle_validates(self, tmp_path: Path) -> None:
        bundle = compile_src(
            tmp_path,
            (
                "class C:\n"
                "    def m(self, *a, k=1, **kw):\n"
                "        yield from a\n"
                "with_stmt = [w for w in 'ab' if w]\n"
                "f = lambda: {**{}, 'x': 1}\n"
            ),
        )
        validate_bundle(bundle)

    def test_roundtrips_through_json(self, tmp_path: Path) -> None:
        bundle = compile_src(tmp_path, "print('hi')\n")
        assert json.loads(dumps(bundle)) == bundle
        assert json.loads(dumps(bundle, pretty=True)) == bundle

    def test_multi_module_bundle(self, tmp_path: Path) -> None:
        (tmp_path / "helper.py").write_text("HELPER = 1\n", encoding="utf-8")
        (tmp_path / "prog.py").write_text("import helper\nprint(helper.HELPER)\n", encoding="utf-8")
        bundle = compile_bundle(str(tmp_path / "prog.py"))
        assert set(bundle["modules"]) == {"prog", "helper"}
        assert bundle["main"] == "prog"
        validate_bundle(bundle)

    def test_stdlib_store_root_precedence(self, tmp_path: Path, monkeypatch) -> None:
        import gzip

        from diaspore import compiler, stdlib_store

        own = tmp_path / "stdlib"
        vendor = tmp_path / "cpython"
        own.mkdir()
        vendor.mkdir()
        (own / "sample.py").write_text("VALUE = 1\n", encoding="utf-8")
        (vendor / "sample.py").write_text("VALUE = 2\n", encoding="utf-8")
        (vendor / "upstream.py").write_text("VALUE = 3\n", encoding="utf-8")
        (vendor / "LICENSE").write_text("PSF license text\n", encoding="utf-8")
        (vendor / "UNICODE_LICENSE").write_text("Unicode license text\n", encoding="utf-8")
        (vendor / "stdlib_manifest.json").write_text("{}\n", encoding="utf-8")
        (own / "ignored.txt").write_text("not a source module\n", encoding="utf-8")
        monkeypatch.setattr(compiler, "STDLIB_DIRS", (str(own), str(vendor)))
        store = stdlib_store.compile_stdlib_store()
        assert set(store["modules"]) == {"sample", "upstream"}
        assert store["modules"]["sample"]["file"] == "<stdlib>/sample.py"
        assert store["modules"]["upstream"]["file"] == "<stdlib>/upstream.py"
        monkeypatch.setattr(stdlib_store, "_store", store)
        path = tmp_path / "stdlib.dsp.json.gz"
        stdlib_store.write_stdlib_store(str(path))
        assert json.loads(gzip.decompress(path.read_bytes())) == store
        entry = tmp_path / "entry.py"
        entry.write_text("import sample\nimport upstream\n", encoding="utf-8")
        bundle = compiler.compile_bundle(str(entry), self_contained=True)
        assert bundle["modules"]["sample"]["file"] == "<stdlib>/sample.py"
        assert bundle["modules"]["upstream"]["file"] == "<stdlib>/upstream.py"
        own_bundle = compiler.compile_bundle(str(own / "sample.py"), stdlib=False)
        assert bundle["modules"]["sample"]["body"] == own_bundle["modules"]["sample"]["body"]
        linked = compiler.compile_bundle(str(entry))
        assert set(linked["modules"]) == {"entry"}
        assert linked["stdlib"] == store["id"]


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
    entry.write_text(
        "import left\nimport right\nprint(left.VALUE + right.VALUE)\n", encoding="utf-8"
    )
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
    {
        "diaspore": 1,
        "main": "m",
        "modules": {"m": {"name": "m", "file": "m.py", "body": [{"op": "nonsense"}]}},
    },
    {
        "diaspore": 1,
        "main": "m",
        "modules": {
            "m": {
                "name": "m",
                "file": "m.py",
                "body": [{"op": "assign", "targets": [], "v": {"op": "name", "id": "x"}}],
            }
        },
    },
    {
        "diaspore": 1,
        "main": "m",
        "modules": {
            "m": {
                "name": "m",
                "file": "m.py",
                "body": [{"op": "pass", "ln": 1, "extra_field": True}],
            }
        },
    },
    {
        "diaspore": 1,
        "main": "m",
        "modules": {
            "m": {
                "name": "m",
                "file": "m.py",
                "body": [{"op": "boolop", "kind": "and", "values": [{"op": "name", "id": "x"}]}],
            }
        },
    },
    {
        "diaspore": 1,
        "main": "m",
        "modules": {
            "m": {
                "name": "m",
                "file": "m.py",
                "body": [
                    {
                        "op": "binop",
                        "kind": "hax",
                        "l": {"op": "name", "id": "x"},
                        "r": {"op": "name", "id": "y"},
                    }
                ],
            }
        },
    },
    {
        "diaspore": 1,
        "main": "m",
        "modules": {"m": {"name": "m", "file": "m.py", "body": [{"op": "return", "v": 42}]}},
    },
]

HOSTILE_BUNDLES += [
    {
        "diaspore": 1,
        "main": "m",
        "modules": {
            "m": {
                "name": "m",
                "file": "m.py",
                "body": [
                    {
                        "op": "func",
                        "name": "f",
                        "params": {"args": ["x"], "posonly": count},
                        "body": [],
                    },
                ],
            }
        },
    }
    for count in (-1, 2, 1.5, True, False, "1", None)
]


class TestValidator:
    @pytest.mark.parametrize("bad", HOSTILE_BUNDLES)
    def test_rejects_malformed_bundles(self, bad: dict) -> None:
        with pytest.raises(BundleValidationError):
            validate_bundle(bad)

    def test_accepts_compiled_bundles(self, tmp_path: Path) -> None:
        bundle = compile_src(
            tmp_path,
            (
                "class C:\n"
                "    def m(self, *a, k=1, **kw):\n"
                "        yield from a\n"
                "match [1]:\n"
                "    case [x]:\n"
                "        pass\n"
                "f = lambda: {**{}, 'x': 1}\n"
            ),
        )
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
