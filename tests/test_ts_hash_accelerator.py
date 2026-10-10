"""Private native hashes preserve incremental state and the upstream public API."""

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from .conftest import TS_DIR, Result, assert_matches, build, run_cpython

_STATE_SOURCE = """
import hashlib

for name in ('md5', 'sha1', 'sha224', 'sha256', 'sha384', 'sha512'):
    value = hashlib.new(name)
    print(name, value.digest().hex(), value.hexdigest())
    for size in (0, 1, 63, 64, 65, 127, 128, 129, 1024):
        chunk = bytes(index % 251 for index in range(size))
        print(value.update(chunk), value.digest().hex(), value.hexdigest())
        print(value.digest() == value.digest())
    other = value.copy()
    other.update(b'copy')
    value.update(b'original')
    print(value.hexdigest(), other.hexdigest(), value is other)
    data = bytearray(b'mutable')
    value.update(data)
    data[:] = b'changed'
    view = memoryview(bytearray(b'contiguous'))
    value.update(view)
    view.release()
    before = value.digest()
    released = memoryview(b'released')
    released.release()
    for invalid in ('text', 3, [1, 2], memoryview(b'abcdef')[::2], released):
        try:
            value.update(invalid)
        except Exception as error:
            print(type(error).__name__, value.digest() == before)
    for invoke in [lambda: value.update(data=b'keyword'), lambda: value.digest(1),
                   lambda: value.hexdigest(1), lambda: value.copy(1)]:
        try:
            invoke()
        except TypeError as error:
            print(type(error).__name__)
    print(value.hexdigest())
"""


@pytest.mark.parametrize("private", [False, True], ids=["public-provider", "private-engine"])
def test_ts_hash_state(tmp_path: Path, ts_vm: list[str], private: bool) -> None:
    entry = tmp_path / "hash_state.py"
    entry.write_text(_STATE_SOURCE, encoding="utf-8")
    expected = run_cpython(entry)
    assert expected.code == 0, expected.err_line
    if private:
        entry.write_text(
            _STATE_SOURCE.replace("import hashlib", "import _diaspore_hash as hashlib"),
            encoding="utf-8",
        )
    bundle = tmp_path / "hash_state.dsp.json"
    bundle.write_text(json.dumps(build(entry)), encoding="utf-8")
    process = subprocess.run(
        [*ts_vm, "run", str(bundle)], capture_output=True, text=True, timeout=120
    )
    assert process.returncode == 0, process.stderr
    lines = process.stderr.strip().splitlines()
    result = Result(process.stdout, process.returncode, lines[-1] if lines else "")
    assert_matches(result, expected, "TypeScript incremental hash state")


def test_ts_hash_engine_boundary_and_fuel(ts_vm: list[str]) -> None:
    script = r"""
import {Interp} from './src/interp.ts';
import {DiasporeError, DiasporeTrap} from './src/values.ts';
const I = new Interp({diaspore:1, main:'m', modules:{m:{name:'m',file:'m.py',body:[]}}});
const module = I.natives.get('_diaspore_hash');
if (module === undefined) throw Error('private native hash module is not registered');
const invoke = (fn, args=[]) => I.drive(I.invoke(fn,args,new Map()));
const method = (value,name,args=[]) => invoke(I.drive(I.getAttr(value,name)),args);
const constructor = module.ns.get('new');
const errors = [];
for (const name of ['unknown', 'SHA256', 3n]) {
  try { invoke(constructor,[name]); }
  catch (error) {
    if (!(error instanceof DiasporeError)) throw error;
    errors.push([error.value.cls.name,I.drive(I.str(error.value))]);
  }
}
const engine = invoke(constructor,['sha256']);
const data = invoke(I.builtins.get('bytearray'),[new Uint8Array(65536)]);
I.maxSteps = I.steps + 10;
let budgetStopped = false;
try { method(engine,'update',[data]); }
catch (error) { if (!(error instanceof DiasporeTrap)) throw error; budgetStopped = true; }
I.maxSteps = 0;
const unchanged = method(engine,'hexdigest');
method(data,'append',[1n]); // Failed updates must release buffer exports.
const before = I.steps;
method(engine,'update',[new Uint8Array(65536)]);
const bulkCost = I.steps-before;
const digest = method(engine,'hexdigest');
console.log(JSON.stringify({errors,budgetStopped,unchanged,bulkCost,digest}));
"""
    process = subprocess.run(
        [ts_vm[0], "--input-type=module", "-e", script],
        cwd=TS_DIR,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert process.returncode == 0, process.stderr
    result = json.loads(process.stdout)
    assert result["errors"] == [
        ["ValueError", "unsupported hash type unknown"],
        ["ValueError", "unsupported hash type SHA256"],
        ["TypeError", "hash name must be a string"],
    ]
    assert result["budgetStopped"] is True
    assert result["unchanged"] == hashlib.sha256().hexdigest()
    assert result["bulkCost"] >= 65536 // 64 + 1
    assert result["digest"] == hashlib.sha256(bytes(65536)).hexdigest()


def test_ts_public_hash_uses_private_engine(tmp_path: Path, ts_vm: list[str]) -> None:
    entry = tmp_path / "hash_engine_selection.py"
    entry.write_text(
        "import hashlib\n"
        "for name in ('md5', 'sha1', 'sha224', 'sha256', 'sha384', 'sha512'):\n"
        "    value = hashlib.new(name)\n"
        "    assert value._engine is not None\n"
        "    print(value.name, value.block_size, value.digest_size)\n",
        encoding="utf-8",
    )
    bundle = tmp_path / "hash_engine_selection.dsp.json"
    bundle.write_text(json.dumps(build(entry)), encoding="utf-8")
    process = subprocess.run(
        [*ts_vm, "run", str(bundle)], capture_output=True, text=True, timeout=120
    )
    assert process.returncode == 0, process.stderr
    expected = []
    for name in ("md5", "sha1", "sha224", "sha256", "sha384", "sha512"):
        value = hashlib.new(name, usedforsecurity=False)
        expected.append(f"{value.name} {value.block_size} {value.digest_size}\n")
    assert process.stdout == "".join(expected)
    assert process.stderr == ""
