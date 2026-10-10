"""TypeScript linked-store isolation and exact content identity acceptance."""

import json
import subprocess
from pathlib import Path

import pytest

from diaspore import compile_bundle
from diaspore.vm import bundle_digest

from .conftest import TS_DIR


@pytest.fixture
def compiled_store(tmp_path: Path) -> dict:
    root = tmp_path / "store"
    root.mkdir()
    entry = root / "sample.py"
    entry.write_text(
        "print('store init')\n"
        "counter = 0\n"
        "BOM = b'\\xef\\xbb\\xbf'\n"
        "def next():\n"
        "    global counter\n"
        "    counter += 1\n"
        "    return counter\n",
        encoding="utf-8",
    )
    module = compile_bundle(str(entry), stdlib=False)["modules"]["sample"]
    module["file"] = "<stdlib>/sample.py"
    payload = {"diaspore_stdlib": 1, "modules": {"sample": module}}
    return dict(payload, id="sha256:" + bundle_digest(payload))


def program(tmp_path: Path, source: str, store: dict | None) -> dict:
    entry = tmp_path / "application.py"
    entry.write_text(source, encoding="utf-8")
    bundle = compile_bundle(str(entry), stdlib=False)
    if store is not None:
        bundle["stdlib"] = store["id"]
    return bundle


def check_ts(tmp_path: Path, ts_vm: list[str], data: dict, checks: str) -> None:
    fixture = tmp_path / "fixture.json"
    fixture.write_text(json.dumps(data), encoding="utf-8")
    imports = (
        'import assert from "node:assert/strict";\n'
        'import {readFileSync} from "node:fs";\n'
        f"import {{Interp}} from {json.dumps((TS_DIR / 'src/interp.ts').as_uri())};\n"
        f"import {{validateStdlibStore}} from {json.dumps((TS_DIR / 'src/stdlib.ts').as_uri())};\n"
        'const {bundle, store, shadow} = JSON.parse(readFileSync(process.argv[1], "utf8"));\n'
        'let out = "", err = "";\n'
        "const opts = {stdlibStore:store, stdout:s=>out+=s, stderr:s=>err+=s};\n"
    )
    result = subprocess.run(
        [ts_vm[0], "--input-type=module", "-e", imports + checks, str(fixture)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_ts_linked_modules_are_lazy_cached_and_per_interpreter(tmp_path, ts_vm, compiled_store):
    bundle = program(
        tmp_path, "def load():\n    import sample\n    return sample.next()\n", compiled_store
    )
    check_ts(
        tmp_path,
        ts_vm,
        {"bundle": bundle, "store": compiled_store},
        """
const first = new Interp(bundle, opts);
assert.equal(first.modules.has("sample"), false);
assert.equal(first.run(), 0, err);
assert.equal(out, "");
assert.equal(first.call("application", "load"), 1);
const module = first.modules.get("sample");
assert.equal(first.call("application", "load"), 2);
assert.equal(first.modules.get("sample"), module);
assert.equal(out, "store init\\n");
const second = new Interp(bundle, opts);
assert.equal(second.run(), 0, err);
assert.equal(second.call("application", "load"), 1);
assert.notEqual(second.modules.get("sample"), module);
assert.equal(out, "store init\\nstore init\\n");
assert.deepEqual(Object.keys(bundle.modules), ["application"]);
""",
    )


def test_ts_program_modules_shadow_store_and_standalone_has_no_fallback(
    tmp_path, ts_vm, compiled_store
):
    bundle = program(tmp_path, "import sample\nprint(sample.next())\n", compiled_store)
    shadow_file = tmp_path / "sample.py"
    shadow_file.write_text("def next():\n    return 42\n", encoding="utf-8")
    shadow = compile_bundle(str(shadow_file), stdlib=False)["modules"]["sample"]
    check_ts(
        tmp_path,
        ts_vm,
        {"bundle": bundle, "store": compiled_store, "shadow": shadow},
        """
const linked = new Interp({...bundle, modules:{...bundle.modules, sample:shadow}}, opts);
assert.equal(linked.run(), 0, err);
assert.equal(out, "42\\n");
const standalone = {...bundle};
delete standalone.stdlib;
const isolated = new Interp(standalone, opts);
assert.equal(isolated.stdlibStore, null);
assert.equal(isolated.moduleCode("sample"), undefined);
assert.equal(isolated.moduleCode("toString"), undefined);
assert.equal(isolated.run(), 1);
assert.match(err, /ModuleNotFoundError/);
assert.equal(out, "42\\n");
""",
    )


def test_ts_store_identity_rejected_before_execution(tmp_path, ts_vm, compiled_store):
    bundle = program(tmp_path, "print('application ran')\n", compiled_store)
    check_ts(
        tmp_path,
        ts_vm,
        {"bundle": bundle, "store": compiled_store},
        """
const wrong = "sha256:" + "0".repeat(64);
assert.throws(() => new Interp({...bundle, stdlib:wrong}, opts), error =>
  error.message.includes(wrong) && error.message.includes(store.id));
assert.equal(out, "");
const corrupt = structuredClone(store);
corrupt.modules.sample.body = [];
assert.throws(() => validateStdlibStore(corrupt), /content id mismatch/);
assert.throws(() => new Interp({...bundle, stdlib:"wrong"}, opts),
  /invalid Diaspore stdlib reference/);
assert.equal(out, "");
""",
    )


def test_ts_stdlib_bridge_copies_module_ir_without_executing_it(tmp_path, ts_vm, compiled_store):
    bundle = program(
        tmp_path,
        (
            "import _diaspore_stdlib as store\n"
            "assert store.module_names() == ['sample']\n"
            "assert store.get_module('missing') is None\n"
            "assert store.get_module('toString') is None\n"
            "module = store.get_module('sample')\n"
            "module['body'].clear()\n"
            "assert len(store.get_module('sample')['body']) > 0\n"
            "print(store.get_id())\n"
        ),
        compiled_store,
    )
    check_ts(
        tmp_path,
        ts_vm,
        {"bundle": bundle, "store": compiled_store},
        """
const runtime = new Interp(bundle, opts);
assert.equal(runtime.run(), 0, err);
assert.equal(out, store.id + "\\n");
assert.equal(runtime.modules.has("sample"), false);
assert.ok(store.modules.sample.body.length > 0);
assert.ok(Object.isFrozen(store.modules.sample.body));
""",
    )


def test_ts_linked_dspb_wire_and_standalone_compatibility(tmp_path, ts_vm, compiled_store):
    bundle = program(tmp_path, "print('linked binary')\n", compiled_store)
    imports = (
        "import {dspbEncode, dspbDecode, isDSPB} from "
        f"{json.dumps((TS_DIR / 'src/dspb.ts').as_uri())};\n"
        f"import {{loadBundle}} from {json.dumps((TS_DIR / 'src/index.ts').as_uri())};\n"
    )
    check_ts(
        tmp_path,
        ts_vm,
        {"bundle": bundle, "store": compiled_store},
        imports
        + """
const linked = dspbEncode(bundle);
assert.equal(linked.subarray(0, 6).toString(), "DSPB02");
assert.equal(isDSPB(linked), true);
assert.deepEqual(dspbDecode(linked), bundle);
assert.deepEqual(loadBundle(linked), bundle);
const standalone = {...bundle};
delete standalone.stdlib;
const portable = dspbEncode(standalone);
assert.equal(portable.subarray(0, 6).toString(), "DSPB01");
assert.deepEqual(dspbDecode(portable), standalone);
for (const bytes of [linked, portable]) {
  assert.throws(() => dspbDecode(Buffer.concat([bytes, Buffer.from([0])])), /trailing bytes/);
  assert.throws(() => dspbDecode(bytes.subarray(0, bytes.length - 1)));
}
for (const stdlib of [null, undefined, false, 1, "", "sha256:" + "A".repeat(64)]) {
  assert.throws(() => dspbEncode({...standalone, stdlib}), /invalid stdlib content id/);
}
assert.throws(() => loadBundle(JSON.stringify({...standalone, stdlib:null})),
  /invalid bundle stdlib/);
const minimal = {diaspore:1, main:"m", stdlib:store.id,
  modules:{m:{name:"m", file:"m", body:[]}}};
const expected = Buffer.concat([
  Buffer.from("DSPB02"), Buffer.from([2, 1, 109, 71]), Buffer.from(store.id),
  Buffer.from([1, 0, 1, 1, 0, 0, 0, 0]),
]);
assert.deepEqual(dspbEncode(minimal), expected);
assert.deepEqual(dspbDecode(expected), minimal);
""",
    )


def test_ts_durable_snapshot_requires_matching_stdlib(tmp_path, ts_vm, compiled_store):
    bundle = program(tmp_path, "print('durable linked')\n", compiled_store)
    imports = (
        "import {buildSnapshot, parseSnapshot, validateSnapshotBundle} from "
        f"{json.dumps((TS_DIR / 'src/flow.ts').as_uri())};\n"
    )
    check_ts(
        tmp_path,
        ts_vm,
        {"bundle": bundle, "store": compiled_store},
        imports
        + """
const runtime = new Interp(bundle, {...opts, durable:true});
const text = buildSnapshot(runtime, bundle);
assert.equal(JSON.parse(text).stdlib, store.id);
const parsed = parseSnapshot(Buffer.from(text));
assert.equal(parsed.stdlib, store.id);
assert.doesNotThrow(() => validateSnapshotBundle(parsed, bundle));
const wrong = "sha256:" + "0".repeat(64);
assert.throws(() => validateSnapshotBundle({...parsed, stdlib:wrong}, bundle),
  error => error.message.includes(wrong) && error.message.includes(store.id));
assert.throws(() => validateSnapshotBundle({...parsed, stdlib:null}, bundle),
  /snapshot stdlib mismatch/);
assert.throws(() => validateSnapshotBundle(parsed, {...bundle, main:"other"}), /different bundle/);
for (const stdlib of [null, 1, "", "sha256:" + "A".repeat(64)]) {
  assert.throws(() => parseSnapshot(Buffer.from(JSON.stringify({...JSON.parse(text), stdlib}))),
    /invalid stdlib content id/);
}
const standalone = {...bundle};
delete standalone.stdlib;
const isolated = new Interp(standalone, {...opts, durable:true});
const standaloneText = buildSnapshot(isolated, standalone);
assert.equal(Object.hasOwn(JSON.parse(standaloneText), "stdlib"), false);
const standaloneParsed = parseSnapshot(Buffer.from(standaloneText));
assert.equal(standaloneParsed.stdlib, null);
assert.doesNotThrow(() => validateSnapshotBundle(standaloneParsed, standalone));
assert.throws(() => validateSnapshotBundle(parsed, standalone), /snapshot stdlib mismatch/);
runtime.close();
isolated.close();
""",
    )


def test_ts_compile_file_modes_and_cli_self_contained(tmp_path, ts_vm):
    entry = tmp_path / "linked.py"
    entry.write_text("import json\nprint(json.dumps([1, 2]))\n", encoding="utf-8")
    plain = tmp_path / "plain.py"
    plain.write_text("print('standalone')\n", encoding="utf-8")
    imports = (
        'import assert from "node:assert/strict";\n'
        f"import {{compileFile, Interp}} from {json.dumps((TS_DIR / 'src/index.ts').as_uri())};\n"
        f"import {{loadStdlibStore}} from {json.dumps((TS_DIR / 'src/stdlib.ts').as_uri())};\n"
    )
    checks = """
const [entry, plain] = process.argv.slice(1);
const linked = compileFile(entry);
assert.equal(linked.stdlib, loadStdlibStore().id);
assert.equal(Object.hasOwn(linked.modules, "json"), false);
const portable = compileFile(entry, {selfContained:true});
assert.equal(Object.hasOwn(portable, "stdlib"), false);
assert.equal(Object.hasOwn(portable.modules, "json"), true);
const disabled = compileFile(plain, {stdlib:false});
assert.equal(Object.hasOwn(disabled, "stdlib"), false);
assert.deepEqual(Object.keys(disabled.modules), ["plain"]);
for (const bundle of [linked, portable]) {
  let out = "", err = "";
  const runtime = new Interp(bundle, {stdout:text=>out+=text, stderr:text=>err+=text});
  assert.equal(runtime.run(), 0, err);
  assert.equal(out, "[1, 2]\\n");
  runtime.close();
}
"""
    result = subprocess.run(
        [
            ts_vm[0],
            "--stack-size=6000",
            "--input-type=module",
            "-e",
            imports + checks,
            str(entry),
            str(plain),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
    )
    assert result.returncode == 0, result.stderr
    output = tmp_path / "portable.dsp.json"
    result = subprocess.run(
        [*ts_vm, "build", str(entry), "--self-contained", "-o", str(output)],
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
    )
    assert result.returncode == 0, result.stderr
    bundle = json.loads(output.read_text(encoding="utf-8"))
    assert "stdlib" not in bundle
    assert "json" in bundle["modules"]


def test_ts_mutable_buffers_cannot_modify_shared_store_bytes(tmp_path, ts_vm, compiled_store):
    bundle = program(
        tmp_path,
        (
            "import sample\n"
            "class BytesOnly:\n"
            "    def __bytes__(self):\n"
            "        return sample.BOM\n"
            "assert bytes(BytesOnly()) is sample.BOM\n"
            "try:\n"
            "    bytearray(BytesOnly())\n"
            "except TypeError:\n"
            "    pass\n"
            "else:\n"
            "    raise AssertionError('bytearray consulted __bytes__')\n"
            "class IterableBytes(BytesOnly):\n"
            "    def __iter__(self):\n"
            "        return iter([1, 2, 3])\n"
            "mutable = bytearray(IterableBytes())\n"
            "assert list(mutable) == [1, 2, 3]\n"
            "mutable[0] = 0\n"
            "mutable = bytearray(sample.BOM)\n"
            "mutable[0] = 0\n"
            "print(sample.BOM.hex())\n"
        ),
        compiled_store,
    )
    check_ts(
        tmp_path,
        ts_vm,
        {"bundle": bundle, "store": compiled_store},
        """
for (let pass = 0; pass < 2; pass++) {
  const runtime = new Interp(bundle, opts);
  assert.equal(runtime.run(), 0, err);
}
assert.equal(out, "store init\\nefbbbf\\nstore init\\nefbbbf\\n");
""",
    )
