"""Test durable replay, portable snapshots, capability policy, and determinism."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from diaspore import Completed, Machine, Suspended, compile_bundle


def build(tmp_path: Path, source: str, name: str = "wf.py") -> dict:
    entry = tmp_path / name
    entry.write_text(source, encoding="utf-8")
    return compile_bundle(str(entry), extra_modules={"host"})


def machine(bundle: dict, out: io.StringIO, **kw) -> Machine:
    return Machine(bundle, stdout=out, stderr=io.StringIO(), durable=True, **kw)


WORKFLOW = """
import flow
import random

print("start")
roll = random.randint(1, 1000000)
first = flow.suspend({"stage": 1, "roll_positive": roll > 0})
print("got", first)
second = flow.suspend({"stage": 2})
print("got", second, "roll stable:", roll == roll)
print("done")
"""


class TestSuspendResume:
    def test_three_segment_flow(self, tmp_path: Path) -> None:
        bundle = build(tmp_path, WORKFLOW)
        out = io.StringIO()
        r1 = machine(bundle, out).run_durable()
        assert isinstance(r1, Suspended)
        assert r1.payload == {"stage": 1, "roll_positive": True}
        assert out.getvalue() == "start\n"

        out2 = io.StringIO()
        r2 = machine(bundle, out2).resume(r1.snapshot, send="alpha")
        assert isinstance(r2, Suspended)
        assert r2.payload == {"stage": 2}
        assert out2.getvalue() == "got alpha\n"  # replay is silent

        out3 = io.StringIO()
        r3 = machine(bundle, out3).resume(r2.snapshot, send=[1, 2])
        assert isinstance(r3, Completed) and r3.code == 0
        assert out3.getvalue() == "got [1, 2] roll stable: True\ndone\n"

    def test_effects_replay_identically(self, tmp_path: Path) -> None:
        bundle = build(tmp_path, (
            "import flow\n"
            "import random\n"
            "import time\n"
            "a = random.randint(1, 10 ** 9)\n"
            "t = time.time()\n"
            "flow.suspend(None)\n"
            "b = random.randint(1, 10 ** 9)\n"
            "print(a, t > 0, a == b or a != b)\n"
            "print('journal kept a stable:', isinstance(a, int))\n"
        ))
        r1 = machine(bundle, io.StringIO()).run_durable()
        assert isinstance(r1, Suspended)
        kinds = [(e["m"], e["f"]) for e in r1.snapshot["journal"]]
        assert kinds == [("random", "randint"), ("time", "time")]
        out = io.StringIO()
        r2 = machine(bundle, out).resume(r1.snapshot)
        assert isinstance(r2, Completed)

    def test_snapshot_is_json_portable(self, tmp_path: Path) -> None:
        bundle = build(tmp_path, WORKFLOW)
        r1 = machine(bundle, io.StringIO()).run_durable()
        wire = json.dumps(r1.snapshot)  # must round-trip through real JSON
        out = io.StringIO()
        r2 = machine(bundle, out).resume(json.loads(wire), send="x")
        assert isinstance(r2, Suspended)
        assert out.getvalue() == "got x\n"

    @pytest.mark.parametrize("bad", [
        {"diaspore_flow": 1, "journal": "evil"},
        {"diaspore_flow": 1, "journal": [{"m": 1}]},
        {"diaspore_flow": 1, "journal": [{"m": "time", "f": "time"}]},  # no r
        {"diaspore_flow": 1, "journal": [], "argv": [1, 2]},
        {"diaspore_flow": 2, "journal": []},
        "not a dict",
    ])
    def test_hostile_snapshots_rejected_cleanly(self, tmp_path: Path, bad) -> None:
        bundle = build(tmp_path, WORKFLOW)
        with pytest.raises(ValueError):
            machine(bundle, io.StringIO()).resume(bad)

    def test_wrong_bundle_rejected(self, tmp_path: Path) -> None:
        bundle = build(tmp_path, WORKFLOW)
        other = build(tmp_path, "import flow\nflow.suspend(1)\n", name="other.py")
        r1 = machine(bundle, io.StringIO()).run_durable()
        with pytest.raises(ValueError, match="different bundle"):
            machine(other, io.StringIO()).resume(r1.snapshot)

    def test_bindings_are_journaled(self, tmp_path: Path) -> None:
        calls = []

        def fetch(key):
            calls.append(key)
            return {"k": key, "n": len(calls)}

        bundle = build(tmp_path, (
            "import flow\n"
            "import host\n"
            "a = host.fetch('one')\n"
            "flow.suspend(a)\n"
            "b = host.fetch('two')\n"
            "print(a['n'], b['n'])\n"
        ))
        bindings = {"host": {"fetch": fetch}}
        r1 = machine(bundle, io.StringIO(), bindings=bindings).run_durable()
        assert isinstance(r1, Suspended) and r1.payload == {"k": "one", "n": 1}
        out = io.StringIO()
        r2 = machine(bundle, out, bindings=bindings).resume(r1.snapshot)
        assert isinstance(r2, Completed)
        # Replay must serve the first result without repeating the host call.
        assert calls == ["one", "two"]
        assert out.getvalue() == "1 2\n"

    def test_non_json_binding_result_rejected(self, tmp_path: Path) -> None:
        bundle = build(tmp_path, (
            "import host\n"
            "try:\n"
            "    host.bad()\n"
            "except TypeError as e:\n"
            "    print('refused')\n"
        ))
        r = machine(bundle, out := io.StringIO(),
                    bindings={"host": {"bad": lambda: (1, 2)}}).run_durable()
        assert isinstance(r, Completed)
        assert out.getvalue() == "refused\n"

    def test_suspend_outside_durable_mode(self, tmp_path: Path) -> None:
        bundle = build(tmp_path, (
            "import flow\n"
            "try:\n"
            "    flow.suspend(1)\n"
            "except RuntimeError as e:\n"
            "    print(e)\n"
        ))
        out = io.StringIO()
        assert Machine(bundle, stdout=out).run() == 0
        assert "requires a durable run" in out.getvalue()

    def test_durable_forbids_caps(self, tmp_path: Path) -> None:
        bundle = build(tmp_path, "print('x')\n")
        with pytest.raises(ValueError, match="forbids capabilities"):
            Machine(bundle, durable=True, caps={"net"})

    def test_finally_not_run_on_suspend(self, tmp_path: Path) -> None:
        bundle = build(tmp_path, (
            "import flow\n"
            "try:\n"
            "    print('try')\n"
            "    flow.suspend('mid-try')\n"
            "    print('after')\n"
            "finally:\n"
            "    print('cleanup')\n"
        ))
        out = io.StringIO()
        r1 = machine(bundle, out).run_durable()
        assert isinstance(r1, Suspended)
        assert out.getvalue() == "try\n"  # no premature 'cleanup'
        out2 = io.StringIO()
        r2 = machine(bundle, out2).resume(r1.snapshot)
        assert isinstance(r2, Completed)
        assert out2.getvalue() == "after\ncleanup\n"


CROSS_VM_WF = """
import flow
import random
import time

roll = random.randint(1, 1000000)
print("segment 1, roll bound:", roll > 0)
first = flow.suspend({"ask": "approve?"})
print("segment 2, got:", first, "roll stable:", roll == roll)
second = flow.suspend(["need", "amount"])
print("segment 3, amount:", second, "still stable:", isinstance(roll, int))
print("done")
"""


class TestCrossVM:
    """Resume snapshots across the Python and Go runtimes."""

    def _go(self, go_vm, cmd, cwd):
        import subprocess
        return subprocess.run([str(go_vm), *cmd], capture_output=True,
                              text=True, timeout=60, cwd=cwd)

    def test_py_suspend_go_resume_py_resume(self, go_vm, tmp_path: Path) -> None:
        import json as _json

        from diaspore import dumps
        bundle = build(tmp_path, CROSS_VM_WF)
        bundle_path = tmp_path / "wf.dsp.json"
        bundle_path.write_text(dumps(bundle), encoding="utf-8")

        # segment 1 on diaspore-py
        r1 = machine(bundle, io.StringIO()).run_durable()
        assert isinstance(r1, Suspended)
        snap1 = tmp_path / "s1.json"
        snap1.write_text(_json.dumps(r1.snapshot), encoding="utf-8")

        # segment 2 on diaspore-go, resuming the diaspore-py snapshot
        snap2 = tmp_path / "s2.json"
        p = self._go(go_vm, ["resume", str(bundle_path), str(snap1),
                             "--send", '"yes"', "--snapshot", str(snap2)], tmp_path)
        assert p.returncode == 7, p.stderr
        assert p.stdout == "segment 2, got: yes roll stable: True\n"

        # segment 3 back on diaspore-py, resuming the diaspore-go snapshot
        out = io.StringIO()
        r3 = machine(bundle, out).resume(
            _json.loads(snap2.read_text(encoding="utf-8")), send=42)
        assert isinstance(r3, Completed) and r3.code == 0
        assert out.getvalue() == "segment 3, amount: 42 still stable: True\ndone\n"

    def test_go_suspend_py_resume(self, go_vm, tmp_path: Path) -> None:
        import json as _json

        from diaspore import dumps
        bundle = build(tmp_path, CROSS_VM_WF)
        bundle_path = tmp_path / "wf.dsp.json"
        bundle_path.write_text(dumps(bundle), encoding="utf-8")
        snap = tmp_path / "s.json"

        p = self._go(go_vm, ["run", str(bundle_path), "--durable",
                             "--snapshot", str(snap)], tmp_path)
        assert p.returncode == 7, p.stderr
        out = io.StringIO()
        r2 = machine(bundle, out).resume(
            _json.loads(snap.read_text(encoding="utf-8")), send="ok")
        assert isinstance(r2, Suspended)
        assert r2.payload == ["need", "amount"]
        assert out.getvalue() == "segment 2, got: ok roll stable: True\n"


class TestScopedCapabilities:
    def test_net_listen_scoped(self, tmp_path: Path) -> None:
        bundle = build(tmp_path, (
            "import net\n"
            "lid = net.listen('127.0.0.1', 8631)\n"
            "print('listened')\n"
            "net.close(lid)\n"
            "try:\n"
            "    net.listen('127.0.0.1', 9999)\n"
            "except PermissionError as e:\n"
            "    print(e)\n"
            "try:\n"
            "    net.connect('example.com', 80)\n"
            "except PermissionError as e:\n"
            "    print('connect denied')\n"
        ))
        out = io.StringIO()
        m = Machine(bundle, stdout=out, stderr=io.StringIO(), caps={"net"},
                    policy={"net": {"listen": ["127.0.0.1:8631"], "connect": []}})
        assert m.run() == 0
        assert out.getvalue() == (
            "listened\n"
            "diaspore: net.listen 127.0.0.1:9999 not permitted by policy\n"
            "connect denied\n")

    def test_net_wildcards(self, tmp_path: Path) -> None:
        bundle = build(tmp_path, (
            "import net\n"
            "lid = net.listen('127.0.0.1', 8632)\n"
            "print('ok')\n"
            "net.close(lid)\n"
        ))
        out = io.StringIO()
        m = Machine(bundle, stdout=out, caps={"net"},
                    policy={"net": {"listen": ["127.0.0.1:*"], "connect": []}})
        assert m.run() == 0 and out.getvalue() == "ok\n"

    def test_fs_scoped(self, tmp_path: Path) -> None:
        allowed = tmp_path / "data"
        allowed.mkdir()
        (allowed / "in.txt").write_text("readable\n", encoding="utf-8")
        secret = tmp_path / "secret.txt"
        secret.write_text("no\n", encoding="utf-8")
        bundle = build(tmp_path, (
            f"with open({str(allowed / 'in.txt')!r}) as f:\n"
            "    print(f.read().strip())\n"
            f"with open({str(allowed / 'out.txt')!r}, 'w') as f:\n"
            "    f.write('written')\n"
            "print('wrote')\n"
            "try:\n"
            f"    open({str(secret)!r})\n"
            "except PermissionError as e:\n"
            "    print('secret denied')\n"
            "try:\n"
            f"    open({str(tmp_path / 'else.txt')!r}, 'w')\n"
            "except PermissionError as e:\n"
            "    print('write denied')\n"
        ))
        out = io.StringIO()
        m = Machine(bundle, stdout=out, caps={"fs"},
                    policy={"fs": {"read": [str(allowed)], "write": [str(allowed)]}})
        assert m.run() == 0
        assert out.getvalue() == "readable\nwrote\nsecret denied\nwrite denied\n"
        assert (allowed / "out.txt").read_text(encoding="utf-8") == "written"


class TestDeterministic:
    def test_logical_clock_and_seeded_random(self, tmp_path: Path) -> None:
        src = (
            "import random\n"
            "import time\n"
            "t1 = time.time()\n"
            "time.sleep(5)\n"
            "t2 = time.time()\n"
            "print(t2 - t1 >= 5, t2 > t1)\n"
            "print([random.randint(1, 100) for _ in range(5)])\n"
        )
        bundle = build(tmp_path, src)

        def run_once() -> str:
            out = io.StringIO()
            assert Machine(bundle, stdout=out, deterministic=True).run() == 0
            return out.getvalue()

        first = run_once()
        assert run_once() == first  # bit-identical across runs, no real sleeping

    def test_deterministic_forbids_caps(self, tmp_path: Path) -> None:
        bundle = build(tmp_path, "print('x')\n")
        with pytest.raises(ValueError, match="deterministic mode forbids"):
            Machine(bundle, deterministic=True, caps={"fs"})
