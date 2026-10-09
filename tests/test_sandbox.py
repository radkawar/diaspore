"""Sandbox guarantees: capabilities, fuel, recursion depth, host bindings."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from diaspore.compiler import compile_bundle
from diaspore.validator import validate_bundle
from diaspore.vm import Machine


def build_src(tmp_path: Path, source: str) -> dict:
    entry = tmp_path / "prog.py"
    entry.write_text(source, encoding="utf-8")
    bundle = compile_bundle(str(entry), extra_modules={"host"})
    validate_bundle(bundle)
    return bundle


def run(bundle: dict, **kw) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = Machine(bundle, stdout=out, stderr=err, **kw).run()
    return code, out.getvalue(), err.getvalue()


class TestCapabilities:
    def test_net_denied_by_default(self, tmp_path: Path) -> None:
        code, out, _ = run(build_src(tmp_path, (
            "import net\n"
            "try:\n"
            "    net.listen('127.0.0.1', 0)\n"
            "except PermissionError as e:\n"
            "    print(e)\n"
        )))
        assert code == 0
        assert out == "diaspore: capability 'net' not granted (run with --allow-net)\n"

    def test_fs_denied_by_default(self, tmp_path: Path) -> None:
        code, out, _ = run(build_src(tmp_path, (
            "try:\n"
            "    open('/etc/hostname')\n"
            "except PermissionError as e:\n"
            "    print('denied')\n"
        )))
        assert code == 0
        assert out == "denied\n"

    def test_fs_granted(self, tmp_path: Path) -> None:
        data = tmp_path / "data.txt"
        data.write_text("from the host\n", encoding="utf-8")
        code, out, _ = run(build_src(tmp_path, (
            f"with open({str(data)!r}) as f:\n"
            "    print(f.read().strip())\n"
        )), caps={"fs"})
        assert (code, out) == (0, "from the host\n")

    def test_pure_modules_always_available(self, tmp_path: Path) -> None:
        code, out, _ = run(build_src(tmp_path, (
            "import math, json\n"
            "print(json.dumps({'ok': math.floor(2.5)}))\n"
        )))
        assert (code, out) == (0, '{"ok": 2}\n')


class TestFuel:
    def test_budget_trap_exit_code(self, tmp_path: Path) -> None:
        code, out, err = run(build_src(tmp_path, (
            "print('start')\n"
            "while True:\n"
            "    pass\n"
        )), max_steps=10_000)
        assert code == 3
        assert out == "start\n"
        assert "step budget exceeded (10000)" in err

    def test_trap_is_uncatchable(self, tmp_path: Path) -> None:
        code, _, err = run(build_src(tmp_path, (
            "try:\n"
            "    while True:\n"
            "        pass\n"
            "except BaseException:\n"
            "    print('caught it')\n"
            "finally:\n"
            "    print('cleanup')\n"
        )), max_steps=1_000)
        assert code == 3
        assert "step budget exceeded" in err

    def test_budget_not_hit(self, tmp_path: Path) -> None:
        bundle = build_src(tmp_path, "print(sum(range(100)))\n")
        m = Machine(bundle, max_steps=100_000, stdout=io.StringIO())
        assert m.run() == 0
        assert 0 < m.steps < 100_000

    def test_wide_builtin_loops_are_metered(self, tmp_path: Path) -> None:
        # Host-speed builtins must still consume the sandbox's fuel budget.
        for prog in ["print(sum(range(10 ** 12)))\n",
                     "print(len(list(range(10 ** 12))))\n",
                     "print(sorted(range(10 ** 9))[0])\n",
                     "print(min(range(10 ** 12)))\n",
                     "print(any(False for _ in range(10 ** 12)))\n"]:
            code, _, err = run(build_src(tmp_path, prog), max_steps=50_000)
            assert code == 3, prog
            assert "step budget exceeded" in err, prog


class TestDepth:
    def test_recursion_error_is_catchable(self, tmp_path: Path) -> None:
        code, out, _ = run(build_src(tmp_path, (
            "def f(n):\n"
            "    return f(n + 1)\n"
            "try:\n"
            "    f(0)\n"
            "except RecursionError as e:\n"
            "    print('capped:', e)\n"
        )), max_depth=50)
        assert (code, out) == (0, "capped: maximum recursion depth exceeded\n")

    def test_depth_allows_legitimate_recursion(self, tmp_path: Path) -> None:
        code, out, _ = run(build_src(tmp_path, (
            "def down(n):\n"
            "    return 0 if n == 0 else 1 + down(n - 1)\n"
            "print(down(40))\n"
        )), max_depth=50)
        assert (code, out) == (0, "40\n")


class TestBindings:
    def test_host_function_binding(self, tmp_path: Path) -> None:
        calls: list[tuple] = []

        def lookup(key: str) -> str:
            calls.append(("lookup", key))
            return {"region": "eu-west-1"}.get(key, "?")

        code, out, _ = run(build_src(tmp_path, (
            "import host\n"
            "print(host.lookup('region'), host.lookup('nope'))\n"
        )), bindings={"host": {"lookup": lookup}})
        assert (code, out) == (0, "eu-west-1 ?\n")
        assert calls == [("lookup", "region"), ("lookup", "nope")]

    def test_binding_exception_surfaces_in_sandbox(self, tmp_path: Path) -> None:
        def boom() -> None:
            raise ValueError("host says no")

        code, out, _ = run(build_src(tmp_path, (
            "import host\n"
            "try:\n"
            "    host.boom()\n"
            "except ValueError as e:\n"
            "    print('caught:', e)\n"
        )), bindings={"host": {"boom": boom}})
        assert (code, out) == (0, "caught: host says no\n")

    def test_host_calls_into_sandbox(self, tmp_path: Path) -> None:
        bundle = build_src(tmp_path, (
            "def transform(items):\n"
            "    return sorted(x * 2 for x in items)\n"
        ))
        m = Machine(bundle, stdout=io.StringIO())
        assert m.run() == 0
        assert m.call("prog", "transform", [3, 1, 2]) == [2, 4, 6]

    def test_sandbox_callback_passed_to_binding(self, tmp_path: Path) -> None:
        def apply_twice(fn, v):
            return fn(fn(v))

        code, out, _ = run(build_src(tmp_path, (
            "import host\n"
            "print(host.apply_twice(lambda x: x + 10, 5))\n"
        )), bindings={"host": {"apply_twice": apply_twice}})
        assert (code, out) == (0, "25\n")

    def test_bundle_module_shadows_binding(self, tmp_path: Path) -> None:
        (tmp_path / "host.py").write_text("value = 'bundled'\n", encoding="utf-8")
        code, out, _ = run(build_src(tmp_path, (
            "import host\n"
            "print(host.value)\n"
        )), bindings={"host": {"value": "bound"}})
        assert (code, out) == (0, "bundled\n")


class TestReSubsetPolicy:
    """Reject regex features unavailable in the Go runtime's RE2 engine."""

    @pytest.mark.parametrize("pattern,fragment", [
        (r"(a)\1", "backreferences"),
        (r"(?=look)", "only (?:...) groups"),
        (r"(?!neg)", "only (?:...) groups"),
        (r"(?P<name>x)", "only (?:...) groups"),
        (r"\Astart", "\\A/\\Z"),
        (r"(unclosed", "invalid pattern"),
    ])
    def test_rejected_patterns(self, tmp_path: Path, pattern: str, fragment: str) -> None:
        code, out, _ = run(build_src(tmp_path, (
            "import re\n"
            "try:\n"
            f"    re.search({pattern!r}, 'x')\n"
            "except ValueError as e:\n"
            "    print(str(e))\n"
        )))
        assert code == 0
        assert fragment in out

    def test_spawn_denied_without_cap(self, tmp_path: Path) -> None:
        code, out, _ = run(build_src(tmp_path, (
            "import tasks\n"
            "try:\n"
            "    tasks.spawn(len, [1])\n"
            "except PermissionError as e:\n"
            "    print(e)\n"
        )))
        assert code == 0
        assert out == "diaspore: capability 'spawn' not granted (run with --allow-spawn)\n"


class TestHostileBundles:
    def test_machine_validates_by_default(self) -> None:
        import pytest

        from diaspore.validator import BundleValidationError

        bad = {"diaspore": 1, "main": "m",
               "modules": {"m": {"name": "m", "file": "m.py",
                                 "body": [{"op": "boolop", "kind": "and",
                                           "values": [], "ln": 1}]}}}
        with pytest.raises(BundleValidationError):
            Machine(bad)

    def test_abandoned_generators_do_not_leak_threads(self, tmp_path: Path) -> None:
        import gc
        import threading
        import time

        before = threading.active_count()
        code, out, _ = run(build_src(tmp_path, (
            "def gen():\n"
            "    try:\n"
            "        n = 0\n"
            "        while True:\n"
            "            yield n\n"
            "            n += 1\n"
            "    finally:\n"
            "        pass\n"
            "for _ in range(50):\n"
            "    g = gen()\n"
            "    next(g)\n"
            "    next(g)\n"
            "print('abandoned 50 generators')\n"
        )))
        assert (code, out) == (0, "abandoned 50 generators\n")
        gc.collect()
        deadline = time.monotonic() + 5
        while threading.active_count() > before and time.monotonic() < deadline:
            time.sleep(0.05)
        assert threading.active_count() <= before + 1

    def test_repetition_allocation_is_metered(self, tmp_path: Path) -> None:
        code, _, err = run(build_src(tmp_path, "s = 'x' * (10 ** 12)\nprint(len(s))\n"),
                           max_steps=100_000)
        assert code == 3
        assert "step budget exceeded" in err

    def test_self_referential_repr_terminates(self, tmp_path: Path) -> None:
        code, out, _ = run(build_src(tmp_path, (
            "l = [1]\n"
            "l.append(l)\n"
            "print(l)\n"
        )))
        assert (code, out) == (0, "[1, [...]]\n")


class TestExitCodes:
    def test_uncaught_is_one(self, tmp_path: Path) -> None:
        code, _, err = run(build_src(tmp_path, "raise RuntimeError('boom')\n"))
        assert code == 1
        assert err.strip().endswith("RuntimeError: boom")

    def test_sys_exit_code(self, tmp_path: Path) -> None:
        code, out, _ = run(build_src(tmp_path, "import sys\nprint('hi')\nsys.exit(9)\n"))
        assert (code, out) == (9, "hi\n")
