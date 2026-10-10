"""Compare unchanged package tests under CPython 3.12 and nested Diaspore VMs.

Run from the repository root:
    uv run --python 3.12 pytest -q -rs tests/test_self_suite.py

Test-harness dependencies and explicit runtime contract exclusions are reported.
Lua excludes host filesystem metadata; TS/Lua exclude inner task-backed generators.
Other missing language, package, parser, filesystem, or stdlib support is a failure,
including failure before assertions.
Generated source preserves retained lines verbatim; original tests are not edited.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from .conftest import ROOT, corpus_entries

FIXTURES = ROOT / "tests" / "self_suite"
SOURCES = ("test_durable.py", "test_sandbox.py", "test_dspb.py", "test_compiler.py")
PREFIX = "SELF_SUITE_RESULT "
TIMEOUT = 180


@dataclass(frozen=True)
class Case:
    source: str
    owner: str
    function: str
    fixtures: tuple[str, ...]
    excluded: str = ""
    spawn: bool = False
    metadata: bool = False

    @property
    def name(self):
        return ".".join(part for part in (self.owner, self.function) if part)

    @property
    def id(self):
        return self.source + "::" + self.name


def _parameter_names(function):
    names = set()
    for decorator in function.decorator_list:
        if not (
            isinstance(decorator, ast.Call)
            and isinstance(decorator.func, ast.Attribute)
            and decorator.func.attr == "parametrize"
        ):
            raise ValueError("runner does not implement decorator: " + ast.unparse(decorator))
        value = ast.literal_eval(decorator.args[0])
        if isinstance(value, str):
            names.update(name.strip() for name in value.split(","))
        else:
            names.update(value)
    return names


def _dependencies(function, owner, tree):
    """Follow source helpers without importing or executing the original suite."""
    globals_ = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    methods = (
        {}
        if owner is None
        else {node.name: node for node in owner.body if isinstance(node, ast.FunctionDef)}
    )
    pending = [function]
    if owner is not None:
        pending.extend(node for node in owner.body if not isinstance(node, ast.FunctionDef))
    seen = set()
    result = []
    while pending:
        node = pending.pop()
        if id(node) in seen:
            continue
        seen.add(id(node))
        result.append(node)
        for child in ast.walk(node):
            if isinstance(child, ast.Name) and child.id in globals_:
                pending.append(globals_[child.id])
            if (
                isinstance(child, ast.Attribute)
                and isinstance(child.value, ast.Name)
                and child.value.id == "self"
                and child.attr in methods
            ):
                pending.append(methods[child.attr])
    return result


def _classify(source, owner, function, tree):
    try:
        parameters = _parameter_names(function)
    except (ValueError, TypeError, IndexError) as error:
        return Case(source, owner.name if owner else "", function.name, (), str(error))
    arguments = function.args.posonlyargs + function.args.args + function.args.kwonlyargs
    fixtures = tuple(arg.arg for arg in arguments if arg.arg not in parameters | {"self"})
    unknown = set(fixtures) - {"tmp_path"}
    reason = "requires an async test runner" if isinstance(function, ast.AsyncFunctionDef) else ""
    if unknown:
        reason = "requires external pytest fixtures: " + ", ".join(sorted(unknown))
        if unknown & {"go_vm", "ts_vm", "lua_vm"}:
            reason += " (real runtime binaries/subprocesses)"
    nodes = [child for dep in _dependencies(function, owner, tree) for child in ast.walk(dep)]
    for node in nodes:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            modules = (
                [alias.name for alias in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
            )
            if any(module.split(".")[0] in {"subprocess", "multiprocessing"} for module in modules):
                reason = "requires host subprocesses or real binaries"
            if "threading" in modules:
                reason = "observes real host threads; not a pure assertion test"
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "importorskip"
        ):
            reason = "requires optional host dependency: " + ast.unparse(node)
    runs_inner = any(
        isinstance(node, ast.Call)
        and (
            isinstance(node.func, ast.Name)
            and node.func.id == "run"
            or isinstance(node.func, ast.Attribute)
            and node.func.attr in {"run", "run_durable", "resume", "call"}
        )
        for node in nodes
    )
    grants_net = any(
        isinstance(node, ast.keyword)
        and node.arg == "caps"
        and any(
            isinstance(value, ast.Constant) and value.value == "net"
            for value in ast.walk(node.value)
        )
        for node in nodes
    )
    if runs_inner and grants_net:
        reason = "requires real network listeners/connections; not a pure test"
    spawn = False
    if runs_inner:
        for node in nodes:
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                try:
                    snippet = ast.parse(node.value)
                except (SyntaxError, ValueError):
                    continue
                if any(
                    isinstance(part, (ast.Yield, ast.YieldFrom, ast.GeneratorExp))
                    for part in ast.walk(snippet)
                ):
                    spawn = True
    # Lua cannot inspect host filesystem metadata. Follow the same helper
    # closure as discovery, without excluding in-memory compiler/validator work.
    metadata_calls = {
        "compile_bundle",
        "FileIO",
        "stat",
        "lstat",
        "readlink",
        "exists",
        "is_file",
        "is_dir",
        "is_symlink",
        "resolve",
    }
    metadata = "tmp_path" in fixtures or any(
        isinstance(node, ast.Call)
        and (
            isinstance(node.func, ast.Name)
            and node.func.id in metadata_calls
            or isinstance(node.func, ast.Attribute)
            and node.func.attr in metadata_calls
        )
        for node in nodes
    )
    return Case(
        source, owner.name if owner else "", function.name, fixtures, reason, spawn, metadata
    )


def _discover():
    cases = []
    for source in SOURCES:
        tree = ast.parse((ROOT / "tests" / source).read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith(
                "test_"
            ):
                cases.append(_classify(source, None, node, tree))
            elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
                for function in node.body:
                    if isinstance(
                        function, (ast.FunctionDef, ast.AsyncFunctionDef)
                    ) and function.name.startswith("test_"):
                        cases.append(_classify(source, node, function, tree))
    return cases


CASES = _discover()
RUNNABLE = [case for case in CASES if not case.excluded]
EXCLUDED = [case for case in CASES if case.excluded]


def _project(source):
    """Blank excluded definitions and unused imports, never rewrite assertions."""
    text = (ROOT / "tests" / source).read_text(encoding="utf-8")
    tree = ast.parse(text)
    lines = text.splitlines(keepends=True)
    exclusions = {(case.owner, case.function) for case in EXCLUDED if case.source == source}

    def blank(node):
        start = min([node.lineno] + [item.lineno for item in getattr(node, "decorator_list", [])])
        for index in range(start - 1, node.end_lineno):
            lines[index] = "\n"

    for node in tree.body:
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and ("", node.name) in exclusions
        ):
            blank(node)
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            tests = [
                item
                for item in node.body
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                and item.name.startswith("test_")
            ]
            if tests and all((node.name, item.name) in exclusions for item in tests):
                blank(node)
            else:
                for item in tests:
                    if (node.name, item.name) in exclusions:
                        blank(item)
    projected = ast.parse("".join(lines))
    used = {node.id for node in ast.walk(projected) if isinstance(node, ast.Name)}
    for node in projected.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if isinstance(node, ast.ImportFrom) and node.module == "__future__":
                continue
            names = {
                alias.asname
                or (alias.name.split(".")[0] if isinstance(node, ast.Import) else alias.name)
                for alias in node.names
            }
            if not names & used:
                blank(node)
    return "".join(lines)


def _process(command, env, label):
    try:
        return subprocess.run(
            command, cwd=ROOT, env=env, input="", capture_output=True, text=True, timeout=TIMEOUT
        )
    except subprocess.TimeoutExpired as error:
        pytest.fail(
            f"{label}: exceeded {TIMEOUT}s\nstdout: {error.stdout!r}\nstderr: {error.stderr!r}"
        )
    except OSError as error:
        pytest.fail(f"{label}: cannot launch: {error}")


def _payload(process, label):
    assert process.returncode == 0, (
        f"{label}: import/runner/runtime blocker (exit {process.returncode})\n"
        f"stdout:\n{process.stdout}\nstderr:\n{process.stderr}"
    )
    records = [
        line[len(PREFIX) :] for line in process.stdout.splitlines() if line.startswith(PREFIX)
    ]
    assert len(records) == 1, (
        f"{label}: missing/duplicate result envelope\n{process.stdout}\n{process.stderr}"
    )
    return json.loads(records[0])


@pytest.fixture(scope="session", autouse=True)
def self_suite_inventory(request):
    reporter = request.config.pluginmanager.getplugin("terminalreporter")
    if reporter:
        reporter.write_line(
            f"self-suite: {len(RUNNABLE)} pure test functions; {len(EXCLUDED)} explicit exclusions"
        )
        for case in EXCLUDED:
            reporter.write_line(f"self-suite excluded {case.id}: {case.excluded}")


@pytest.fixture(scope="session")
def self_suite_sources(tmp_path_factory):
    assert sys.implementation.name == "cpython" and sys.version_info[:2] == (3, 12), (
        "self-suite ground truth requires CPython 3.12; use uv run --python 3.12 pytest"
    )
    workspaces = {}
    for source in SOURCES:
        workspace = tmp_path_factory.mktemp("self-suite-" + Path(source).stem)
        package = workspace / "suite_case"
        package.mkdir()
        (package / "__init__.py").write_text(_project(source), encoding="utf-8")
        # Resolve the original .conftest import without bundling pytest's host
        # build/process fixtures. The corpus list comes from the real helper.
        entries = [str(path) for path in corpus_entries()]
        catalog_source = (
            "from pathlib import Path\n\ndef corpus_entries():\n"
            f"    return [Path(value) for value in {entries!r}]\n"
        )
        (package / "conftest.py").write_text(catalog_source, encoding="utf-8")
        manifest = [
            (case.name, case.owner, case.function, list(case.fixtures))
            for case in RUNNABLE
            if case.source == source
        ]
        (workspace / "self_suite_manifest.py").write_text(
            f"TESTS = {manifest!r}\n", encoding="utf-8"
        )
        for name in ("driver.py", "pytest.py"):
            (workspace / name).write_text(
                (FIXTURES / name).read_text(encoding="utf-8"), encoding="utf-8"
            )
        env = dict(
            os.environ,
            PYTHONPATH=os.pathsep.join([str(workspace), str(ROOT / "src")]),
            PYTHONHASHSEED="0",
            PYTHONDONTWRITEBYTECODE="1",
        )
        listing = _process(
            [sys.executable, str(workspace / "driver.py"), "--list"],
            env,
            "CPython discovery " + source,
        )
        counts = _payload(listing, "CPython discovery " + source)
        assert set(counts) == {item[0] for item in manifest}, "runner omitted discovered tests"
        assert all(isinstance(count, int) and count > 0 for count in counts.values()), counts
        workspaces[source] = (workspace, env, counts)
    return workspaces


@pytest.fixture(scope="session")
def self_suite_bundles(self_suite_sources):
    cache = {}

    def bundle_for(source):
        if source not in cache:
            workspace, env, _ = self_suite_sources[source]
            output = workspace / "suite.dsp.json"
            command = [
                sys.executable,
                "-m",
                "diaspore.cli",
                "build",
                str(workspace / "driver.py"),
                "--path",
                str(ROOT / "src"),
                "--path",
                str(workspace),
                "-o",
                str(output),
            ]
            result = _process(command, env, "package compilation " + source)
            cache[source] = (output, result)
        output, result = cache[source]
        assert result.returncode == 0, (
            f"{source}: package compilation blocker (--path src)\n{result.stdout}\n{result.stderr}"
        )
        bundle = json.loads(output.read_text(encoding="utf-8"))
        assert {"diaspore", "diaspore.compiler", "diaspore.vm"} <= set(bundle["modules"]), (
            "self-suite must compile the actual package, "
            "not call the host package through a binding"
        )
        return output

    return bundle_for


@pytest.fixture(scope="session")
def self_suite_expected(self_suite_sources, tmp_path_factory):
    cache = {}

    def expected_for(case):
        if case.id not in cache:
            workspace, env, counts = self_suite_sources[case.source]
            root = tmp_path_factory.mktemp("self-suite-cpython")
            for index in range(counts[case.name]):
                (root / str(index)).mkdir()
            command = [sys.executable, str(workspace / "driver.py"), case.name, str(root)]
            result = _payload(_process(command, env, "CPython " + case.id), "CPython " + case.id)
            expected_keys = [(case.name, index) for index in range(counts[case.name])]
            assert [(row["case"], row["index"]) for row in result] == expected_keys
            assert all(row["status"] == "pass" for row in result), (
                f"CPython baseline failure in unchanged assertions: {case.id}\n"
                f"{json.dumps(result, indent=2)}"
            )
            cache[case.id] = result
        return cache[case.id]

    return expected_for


@pytest.mark.parametrize("case", EXCLUDED, ids=lambda case: case.id)
def test_self_suite_exclusion(case):
    pytest.skip(case.excluded)


@pytest.mark.parametrize("case", RUNNABLE, ids=lambda case: case.id)
@pytest.mark.parametrize("runtime", ["py", "go", "ts", "lua"])
def test_self_suite(
    case,
    runtime,
    self_suite_sources,
    self_suite_expected,
    self_suite_bundles,
    tmp_path,
    request,
    record_property,
):
    if runtime == "lua" and case.metadata:
        pytest.skip("diaspore-lua cannot read file metadata")
    if case.spawn and runtime in {"ts", "lua"}:
        pytest.skip("source executes an inner generator; the package VM requires tasks/spawn")
    expected = self_suite_expected(case)
    bundle = self_suite_bundles(case.source)
    workspace, env, counts = self_suite_sources[case.source]
    for index in range(counts[case.name]):
        (tmp_path / str(index)).mkdir()
    if runtime == "py":
        command = [sys.executable, "-m", "diaspore.cli"]
    elif runtime == "go":
        command = [str(request.getfixturevalue("go_vm"))]
    else:
        command = list(request.getfixturevalue(runtime + "_vm"))
    command += [
        "run",
        str(bundle),
        "--allow-fs",
        "read:" + str(ROOT),
        "--allow-fs",
        "read:" + str(workspace),
        "--allow-fs",
        "read:" + str(tmp_path),
        "--allow-fs",
        "write:" + str(tmp_path),
        "--max-steps",
        "100000000",
    ]
    if case.spawn:
        command.append("--allow-spawn")
    command += ["--", case.name, str(tmp_path)]
    label = "diaspore-" + runtime + " " + case.id
    actual = _payload(_process(command, env, label), label)
    record_property("self_suite_source", case.id)
    record_property("self_suite_parameters", len(expected))
    record_property("self_suite_outcomes", json.dumps(actual))
    assert [(row["case"], row["index"], row["status"]) for row in actual] == [
        (row["case"], row["index"], row["status"]) for row in expected
    ], f"{label}: differs from CPython 3.12\n{json.dumps(actual, indent=2)}"
