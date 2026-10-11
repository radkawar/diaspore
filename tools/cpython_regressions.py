#!/usr/bin/env python3
"""Detect CPython 3.12 regression gaps without rewriting upstream test sources.

Run with a CPython 3.12 interpreter. The default selection is the language and
pure-library regression set; --all scans every test module/package below
Lib/test, including nested packages. --module accepts repeated dotted names.
Results, logs, original-source inventory, and bundles go under build/.

Examples:
    uv run --python 3.12 python tools/cpython_regressions.py --all
    uv run --python 3.12 python tools/cpython_regressions.py --module test_dict
    uv run --python 3.12 python tools/cpython_regressions.py --all \
        --write-baseline tests/cpython_baseline.json
    uv run --python 3.12 python tools/cpython_regressions.py --all \
        --baseline tests/cpython_baseline.json

The baseline stores observed passes, never estimates from static test names.
Without --baseline the exit status signals detected gaps; with --baseline it
signals regressions, allowing existing gaps to remain visible in the scoreboard.
The tool never builds runtimes. Prepare build/diaspore-go separately.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import secrets
import subprocess
import sys
import tarfile
import traceback
import unittest
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = "3.12.12"
DEFAULT_MODULES = tuple(
    "test_" + name
    for name in (
        "grammar",
        "scope",
        "class",
        "exceptions",
        "generators",
        "types",
        "dict",
        "list",
        "set",
        "string_literals",
        "fstring",
        "unpack",
        "with",
        "contextlib",
        "functools",
        "itertools",
        "collections",
        "json",
    )
)
FIXTURES = ROOT / "tools" / "cpython_fixtures"
COUNTS = ("passed", "failed", "errored", "skipped", "expected_failure", "unexpected_success")


def emit(token, kind, **fields):
    """Emit one flushed, token-prefixed detector event."""
    print(token + json.dumps({"kind": kind, **fields}, ensure_ascii=True), flush=True)


def flatten(suite):
    """Yield individual test cases from nested unittest suites."""
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from flatten(item)
        else:
            yield item


def baseline_worker(lib, module, token):
    """Discover and execute a module with the native CPython test library."""
    # Only test sources use this path; the baseline keeps its native stdlib.
    import importlib.util

    package_path = Path(lib) / "test" / "__init__.py"
    spec = importlib.util.spec_from_file_location(
        "test", package_path, submodule_search_locations=[str(package_path.parent)]
    )
    package = importlib.util.module_from_spec(spec)
    sys.modules["test"] = package
    emit(token, "stage", stage="import")
    try:
        spec.loader.exec_module(package)
        target = importlib.import_module(module)
    except unittest.SkipTest as error:
        emit(token, "module_skip", reason=str(error))
        return
    except BaseException:
        emit(token, "stage_error", stage="import", reason=traceback.format_exc())
        return
    emit(token, "stage", stage="discovery")
    loader = unittest.TestLoader()
    try:
        if hasattr(target, "test_main"):
            raise RuntimeError("test_main is unsupported by CPython 3.12 regrtest")
        suite = loader.loadTestsFromModule(target)
        if loader.errors:
            raise RuntimeError("\n".join(loader.errors))
        inventory = []
        for index, test in enumerate(flatten(suite)):
            cls = type(test)
            owner = sys.modules.get(cls.__module__)
            aliases = [key for key, value in vars(owner).items() if value is cls] if owner else []
            # Resolve generated classes by their actual exported alias, not __name__.
            class_path = sorted(aliases)[0] if aliases else cls.__qualname__
            inventory.append(
                {
                    "index": index,
                    "id": test.id(),
                    "module": cls.__module__,
                    "class": class_path,
                    "method": getattr(test, "_testMethodName", None),
                    "standard_case": isinstance(test, unittest.TestCase)
                    and not isinstance(test, unittest.FunctionTestCase),
                }
            )
        emit(token, "inventory", tests=inventory)
    except BaseException:
        emit(token, "stage_error", stage="discovery", reason=traceback.format_exc())
        return

    class Result(unittest.TestResult):
        def __init__(self):
            super().__init__()
            self.index = -1
            self.active = None

        def startTest(self, test):
            super().startTest(test)
            self.index += 1
            self.active = test
            emit(token, "start", index=self.index, id=test.id())

        def stopTest(self, test):
            super().stopTest(test)
            self.active = None

        def record(self, test, outcome, reason=""):
            index = self.index if test is self.active else -1
            emit(token, "outcome", index=index, id=str(test), outcome=outcome, reason=reason)

        def addSuccess(self, test):
            super().addSuccess(test)
            self.record(test, "passed")

        def addFailure(self, test, err):
            super().addFailure(test, err)
            self.record(test, "failed", self._exc_info_to_string(err, test))

        def addError(self, test, err):
            super().addError(test, err)
            self.record(test, "errored", self._exc_info_to_string(err, test))

        def addSkip(self, test, reason):
            super().addSkip(test, reason)
            self.record(test, "skipped", reason)

        def addExpectedFailure(self, test, err):
            super().addExpectedFailure(test, err)
            self.record(test, "expected_failure", self._exc_info_to_string(err, test))

        def addUnexpectedSuccess(self, test):
            super().addUnexpectedSuccess(test)
            self.record(test, "unexpected_success")

        def addSubTest(self, test, subtest, err):
            super().addSubTest(test, subtest, err)
            if err is not None:
                outcome = "failed" if issubclass(err[0], test.failureException) else "errored"
                self.record(test, outcome, self._exc_info_to_string(err, test))

    emit(token, "stage", stage="tests")
    try:
        result = Result()
        suite.run(result)
        emit(token, "complete")
    except BaseException:
        emit(token, "stage_error", stage="tests", reason=traceback.format_exc())


def subprocess_record(command, cwd, timeout, token, log):
    """Run a detector process and retain logs, events, and timeout status."""
    env = dict(os.environ, PYTHONHASHSEED="0", PYTHONDONTWRITEBYTECODE="1")
    try:
        proc = subprocess.run(
            command,
            cwd=cwd,
            env=env,
            input="",
            text=True,
            errors="replace",
            capture_output=True,
            timeout=timeout,
        )
        stdout, stderr, code, timed_out = proc.stdout, proc.stderr, proc.returncode, False
    except subprocess.TimeoutExpired as error:
        stdout, stderr = error.stdout or b"", error.stderr or b""
        stdout = stdout.decode(errors="replace") if isinstance(stdout, bytes) else stdout
        stderr = stderr.decode(errors="replace") if isinstance(stderr, bytes) else stderr
        code, timed_out = None, True
    except OSError as error:
        stdout, stderr, code, timed_out = "", str(error), None, False
    log.with_suffix(".stdout").write_text(stdout)
    log.with_suffix(".stderr").write_text(stderr)
    events = []
    for line in stdout.splitlines():
        if line.startswith(token):
            try:
                events.append(json.loads(line[len(token) :]))
            except json.JSONDecodeError:
                pass
    return {
        "returncode": code,
        "timeout": timed_out,
        "events": events,
        "stdout_log": str(log.with_suffix(".stdout")),
        "stderr_log": str(log.with_suffix(".stderr")),
    }


def summarize(record, expected):
    """Classify detector outcomes without counting missing execution as success."""
    events = record.pop("events")
    outcomes = {}
    # Multiple failures/cleanup errors can belong to one test. Never turn them
    # into several executed tests, or let a later success erase a failure.
    rank = {
        "passed": 0,
        "skipped": 1,
        "expected_failure": 2,
        "unexpected_success": 3,
        "failed": 4,
        "errored": 5,
    }
    started = set()
    stage = "startup"
    complete = False
    errors = []
    inventory = None
    module_skip = None
    for event in events:
        kind = event.get("kind")
        if kind == "stage":
            stage = event["stage"]
        elif kind == "inventory":
            inventory = event["tests"]
        elif kind == "start":
            started.add(event["index"])
        elif kind == "outcome":
            key = event["index"]
            # Fixture errors before startTest are module errors, not test passes.
            if key not in started:
                errors.append(event)
            elif key not in outcomes or rank[event["outcome"]] > rank[outcomes[key]["outcome"]]:
                outcomes[key] = event
        elif kind == "stage_error":
            errors.append(event)
        elif kind == "module_skip":
            module_skip = event["reason"]
        elif kind == "complete":
            complete = True
    expected = len(inventory) if inventory is not None else expected
    counts = {name: 0 for name in COUNTS}
    for event in outcomes.values():
        counts[event["outcome"]] += 1
    interrupted = len(started - outcomes.keys())
    counts["errored"] += interrupted
    executed = len(started)
    unexecuted = max(0, expected - executed) if expected is not None else None
    if module_skip is not None:
        status = "skipped"
    elif errors:
        status = errors[0].get("stage", stage) + "_failure"
    elif record["timeout"]:
        status = stage + "_timeout"
    elif not complete or record["returncode"] != 0:
        status = stage + "_failure"
    elif counts["failed"] or counts["errored"] or counts["unexpected_success"]:
        status = "failed"
    elif unexecuted:
        status = "incomplete"
    elif counts["passed"]:
        status = "passed"
    elif counts["skipped"] or counts["expected_failure"]:
        status = "skipped"
    else:
        status = "no_tests"
    return {
        **record,
        "status": status,
        "tests": counts,
        "discovered": expected,
        "executed": executed,
        "unexecuted": unexecuted,
        "module_skip": module_skip,
        "errors": errors,
        "outcomes": list(outcomes.values()),
        "inventory": inventory,
    }


def validate_source(lib):
    """Authenticate a CPython library against the pinned release version."""
    header = lib.parent / "Include/patchlevel.h"
    if header.is_file():
        versions = [
            line.split('"')[1]
            for line in header.read_text().splitlines()
            if line.startswith("#define PY_VERSION ")
        ]
        if versions != [VERSION]:
            raise ValueError(f"source must be CPython {VERSION}; found {versions}")
    elif (
        lib.resolve() != (Path(sys.base_prefix) / "lib/python3.12").resolve()
        or ".".join(map(str, sys.version_info[:3])) != VERSION
    ):
        raise ValueError("cannot authenticate source version: use a pinned CPython checkout")
    return lib


def source_lib(explicit):
    """Locate or download the authenticated CPython regression library."""
    if explicit:
        path = Path(explicit).resolve()
        for candidate in (path / "Lib", path, path.parent if path.name == "test" else path):
            if (candidate / "test" / "test_grammar.py").is_file():
                return validate_source(candidate)
        raise ValueError("--source must contain Lib/test or be its Lib directory")
    candidates = [
        Path.home() / "dev/minor/clones/cpython/Lib",
        ROOT / "build/cpython" / ("Python-" + VERSION) / "Lib",
        ROOT / "build/cpython" / ("cpython-" + VERSION) / "Lib",
        Path(sys.base_prefix) / "lib/python3.12",
    ]
    for path in candidates:
        if (path / "test/test_grammar.py").is_file():
            return validate_source(path)
    destination = ROOT / "build/cpython"
    destination.mkdir(parents=True, exist_ok=True)
    archive = destination / ("Python-" + VERSION + ".tgz")
    temporary = archive.with_suffix(".download")
    url = f"https://www.python.org/ftp/python/{VERSION}/Python-{VERSION}.tgz"
    urllib.request.urlretrieve(url, temporary)
    expected_hash = "487c908ddf4097a1b9ba859f25fe46d22ccaabfb335880faac305ac62bffb79b"
    if hashlib.sha256(temporary.read_bytes()).hexdigest() != expected_hash:
        raise ValueError("official CPython archive SHA256 mismatch")
    temporary.replace(archive)
    with tarfile.open(archive) as source:
        source.extractall(destination, filter="data")
    return destination / ("Python-" + VERSION) / "Lib"


def discover(lib, requested, all_modules):
    """Select regression modules, expanding requested test packages recursively."""
    root = lib / "test"
    candidates = {}
    # Include test packages and every nested test_*.py, not just top-level files.
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(lib)
        if path.name == "__init__.py":
            if not path.parent.name.startswith("test_"):
                continue
            parts = relative.parts[:-1]
        elif path.name.startswith("test_"):
            parts = (*relative.parts[:-1], path.stem)
        else:
            continue
        if all(part.isidentifier() for part in parts):
            candidates[".".join(parts)] = path
    if all_modules:
        return candidates
    selected = {}
    for name in requested or DEFAULT_MODULES:
        name = name if name.startswith("test.") else "test." + name
        matches = {
            key: path
            for key, path in candidates.items()
            if key == name or key.startswith(name + ".")
        }
        if not matches:
            raise ValueError(f"no CPython regression module matches {name!r}")
        selected.update(matches)
    return dict(sorted(selected.items()))


def compile_worker(entry, overlay, bundle_path, report_path):
    """Compile a regression entry and record genuine import or compile failures."""
    sys.path.insert(0, os.environ.get("DIASPORE_SOURCE", str(ROOT / "src")))
    from diaspore.compiler import compile_bundle

    try:
        unresolved = {}
        bundle = compile_bundle(
            entry, search_paths=(str(FIXTURES), overlay), unresolved_out=unresolved
        )
        Path(bundle_path).write_text(json.dumps(bundle))
        report = {"ok": True, "unresolved_imports": unresolved}
    except BaseException:
        report = {"ok": False, "error": traceback.format_exc()}
    Path(report_path).write_text(json.dumps(report))


def py_worker(bundle_path):
    """Execute a bundle in the Python VM and close its resources."""
    sys.stdout.reconfigure(line_buffering=True)
    sys.path.insert(0, os.environ.get("DIASPORE_SOURCE", str(ROOT / "src")))
    from diaspore.vm import Machine

    bundle = json.loads(Path(bundle_path).read_text())
    machine = Machine(bundle, argv=[bundle_path])
    try:
        code = machine.run()
    finally:
        if hasattr(machine, "close"):
            machine.close()
    raise SystemExit(code)


def baseline_data(score):
    """Extract versioned pass counts and statuses for the regression ratchet."""
    return {
        "schema": 1,
        "cpython": score["cpython"],
        "source_digest": score["source_digest"],
        "modules": {
            name: {
                runtime: {"passed": value["tests"]["passed"], "status": value["status"]}
                for runtime, value in row["runtimes"].items()
            }
            for name, row in score["modules"].items()
        },
    }


def ratchet(score, path):
    """Return regressions against the recorded module and runtime baseline."""
    old = json.loads(path.read_text())
    failures = []
    if old.get("schema") != 1 or old.get("cpython") != score["cpython"]:
        failures.append("baseline schema or CPython pin changed")
    if old.get("source_digest") != score["source_digest"]:
        failures.append("upstream selected-source digest changed; regenerate baseline explicitly")
    for module, runtimes in old["modules"].items():
        for runtime, previous in runtimes.items():
            if runtime == "cpython":
                # The reference interpreter's counts depend on the host (locale,
                # terminal, network); it is recorded for context, never gated.
                continue
            current = score["modules"].get(module, {}).get("runtimes", {}).get(runtime)
            if current is None:
                failures.append(f"{module}/{runtime}: missing measurement")
            elif current["tests"]["passed"] < previous["passed"]:
                failures.append(
                    f"{module}/{runtime}: passes {previous['passed']} -> "
                    f"{current['tests']['passed']}"
                )
            elif (
                previous["status"] in ("passed", "no_tests")
                and current["status"] != previous["status"]
            ):
                failures.append(f"{module}/{runtime}: {previous['status']} -> {current['status']}")
    return failures


def main():
    """Run selected regressions and write scoreboards and optional baselines."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--source", help="CPython checkout, Lib directory, or Lib/test directory")
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument(
        "--all", action="store_true", help="scan the complete Lib/test tree recursively"
    )
    selection.add_argument(
        "--module", action="append", help="module or test package; packages expand recursively"
    )
    parser.add_argument("--runtimes", nargs="+", choices=("py", "go"), default=["py", "go"])
    parser.add_argument("--go", default=str(ROOT / "build/diaspore-go"))
    parser.add_argument(
        "--diaspore-source",
        type=Path,
        default=ROOT / "src",
        help="Diaspore source root, for measuring a preserved baseline",
    )
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--output", type=Path, default=ROOT / "build/cpython-regressions")
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--write-baseline", type=Path)
    args = parser.parse_args()
    os.environ["DIASPORE_SOURCE"] = str(args.diaspore_source.resolve())
    if sys.version_info[:2] != (3, 12):
        parser.error("run this detector with CPython 3.12 (uv run --python 3.12 python ...)")
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    lib = source_lib(args.source)
    selected = discover(lib, args.module, args.all)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    # Restrict compiler fallback to test sources: never substitute upstream Lib
    # modules for the Diaspore stdlib being measured.
    overlay = output / "upstream"
    overlay.mkdir(exist_ok=True)
    test_link = overlay / "test"
    if test_link.is_symlink():
        test_link.unlink()
    elif test_link.exists():
        parser.error(f"reserved source link already exists: {test_link}")
    test_link.symlink_to(lib / "test", target_is_directory=True)
    digest = hashlib.sha256()
    source_inventory = {}
    for name, path in selected.items():
        content = path.read_bytes()
        fingerprint = hashlib.sha256(content).hexdigest()
        source_inventory[name] = {"path": str(path), "sha256": fingerprint}
        digest.update(name.encode() + b"\0" + content + b"\0")
    score = {
        "schema": 1,
        "cpython": VERSION,
        "host_python": sys.version,
        "source": str(lib),
        "source_digest": digest.hexdigest(),
        "selection": "all" if args.all else list(args.module or DEFAULT_MODULES),
        "sources": source_inventory,
        "modules": {},
    }
    # The old shim has assertions that silently omit part of their contract.
    # Refuse those calls in this detector only; do not repair or bless the shim.
    shim = args.diaspore_source / "diaspore/stdlib/unittest.py"
    guards = []
    if shim.is_file():
        tree = ast.parse(shim.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "assertRaisesRegex":
                if "regex" not in {
                    item.id for item in ast.walk(node) if isinstance(item, ast.Name)
                }:
                    guards.append("assertRaisesRegex")
            if isinstance(node, ast.FunctionDef) and node.name == "assertCountEqual":
                if any(
                    isinstance(item, ast.Name) and item.id == "sorted" for item in ast.walk(node)
                ):
                    guards.append("assertCountEqual")
    score["detector_assertion_guards"] = guards
    for module, source in selected.items():
        work = output / module
        work.mkdir(exist_ok=True)
        token = "CPYTHON_DETECTOR_" + secrets.token_hex(16) + ":"
        host = subprocess_record(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "--worker-baseline",
                str(lib),
                module,
                token,
            ],
            work,
            args.timeout,
            token,
            work / "cpython",
        )
        baseline = summarize(host, None)
        inventory = baseline["inventory"]
        entry = work / "detector_entry.py"
        imports = sorted({module} | {test["module"] for test in inventory or []})
        import_marker = token + json.dumps({"kind": "stage", "stage": "import"})
        lines = [
            f"print({import_marker!r})",
            "import detector_runner",
            "modules = {}",
            f"detector_runner.configure({token!r}, {guards!r})",
            "try:",
        ]
        for index, name in enumerate(imports):
            lines += [
                f"    import {name} as imported_{index}",
                f"    modules[{name!r}] = imported_{index}",
            ]
        lines += [
            "except BaseException as error:",
            "    detector_runner.import_error(error)",
            "else:",
            f"    detector_runner.run(modules, {inventory!r}, {module!r})",
        ]
        entry.write_text("\n".join(lines) + "\n")
        bundle = work / "suite.dsp.json"
        compile_report = work / "compile.json"
        if compile_report.exists():
            compile_report.unlink()
        compilation_proc = subprocess_record(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "--worker-compile",
                str(entry),
                str(overlay),
                str(bundle),
                str(compile_report),
            ],
            ROOT,
            args.timeout,
            token,
            work / "compiler",
        )
        compilation = (
            json.loads(compile_report.read_text())
            if compile_report.exists()
            else {
                "ok": False,
                "error": "compiler process did not produce a report",
                "timeout": compilation_proc["timeout"],
                "stderr_log": compilation_proc["stderr_log"],
            }
        )
        row = {"source": str(source), "compilation": compilation, "runtimes": {"cpython": baseline}}
        for runtime in args.runtimes:
            expected = len(inventory) if inventory is not None else None
            if not compilation["ok"]:
                row["runtimes"][runtime] = {
                    "status": "compilation_failure",
                    "tests": dict.fromkeys(COUNTS, 0),
                    "discovered": expected,
                    "executed": 0,
                    "unexecuted": expected,
                }
                continue
            command = (
                [sys.executable, str(Path(__file__).resolve()), "--worker-py", str(bundle)]
                if runtime == "py"
                else [str(Path(args.go).resolve()), "run", str(bundle)]
            )
            result = subprocess_record(command, work, args.timeout, token, work / runtime)
            row["runtimes"][runtime] = summarize(result, expected)
        score["modules"][module] = row
        (work / "result.json").write_text(json.dumps(row, indent=2) + "\n")
        print(
            module
            + " "
            + " ".join(
                f"{name}={result['status']}:{result['tests']['passed']}"
                for name, result in row["runtimes"].items()
            ),
            flush=True,
        )
    totals = {}
    for runtime in ["cpython", *args.runtimes]:
        rows = [row["runtimes"][runtime] for row in score["modules"].values()]
        statuses = {}
        for row in rows:
            statuses[row["status"]] = statuses.get(row["status"], 0) + 1
        totals[runtime] = {
            "modules": statuses,
            "tests": {key: sum(row["tests"][key] for row in rows) for key in COUNTS},
            "unexecuted": sum(row["unexecuted"] or 0 for row in rows),
            "unknown_inventory_modules": sum(row["discovered"] is None for row in rows),
        }
    score["totals"] = totals
    regressions = ratchet(score, args.baseline) if args.baseline else []
    score["regressions"] = regressions
    (output / "scoreboard.json").write_text(json.dumps(score, indent=2) + "\n")
    summary = ["runtime\tmodule_status\tmodules"]
    for runtime, data in totals.items():
        summary.extend(
            f"{runtime}\t{status}\t{count}" for status, count in sorted(data["modules"].items())
        )
    summary += ["", "runtime\t" + "\t".join(COUNTS) + "\tunexecuted\tunknown_inventory_modules"]
    for runtime, data in totals.items():
        summary.append(
            runtime
            + "\t"
            + "\t".join(str(data["tests"][key]) for key in COUNTS)
            + f"\t{data['unexecuted']}\t{data['unknown_inventory_modules']}"
        )
    (output / "scoreboard.tsv").write_text("\n".join(summary) + "\n")
    if args.write_baseline:
        args.write_baseline.parent.mkdir(parents=True, exist_ok=True)
        args.write_baseline.write_text(
            json.dumps(baseline_data(score), indent=2, sort_keys=True) + "\n"
        )
    print(json.dumps(totals, indent=2))
    for failure in regressions:
        print("REGRESSION: " + failure, file=sys.stderr)
    if args.baseline:
        return int(bool(regressions))
    return int(
        any(
            row["status"] not in ("passed", "skipped", "no_tests")
            for module in score["modules"].values()
            for row in module["runtimes"].values()
        )
    )


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--worker-baseline":
        baseline_worker(*sys.argv[2:])
    elif len(sys.argv) > 1 and sys.argv[1] == "--worker-compile":
        compile_worker(*sys.argv[2:])
    elif len(sys.argv) > 1 and sys.argv[1] == "--worker-py":
        py_worker(*sys.argv[2:])
    else:
        raise SystemExit(main())
