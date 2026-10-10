"""Class metadata and lexical bindings survive TypeScript DSPB transport."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from diaspore import compile_bundle
from diaspore.dspb import decode, encode

from .conftest import TS_DIR, Result, assert_matches, run_cpython


@pytest.fixture
def node_command() -> list[str]:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js not installed")
    return [node, "--experimental-strip-types", "--no-warnings"]


@pytest.mark.parametrize("linked", [False, True], ids=["standalone", "linked"])
@pytest.mark.parametrize(
    "binding",
    [None, "C", "_Owner__C"],
    ids=["absent", "explicit-name", "private"],
)
@pytest.mark.parametrize("with_kwargs", [False, True], ids=["no-keywords", "keywords"])
def test_ts_class_binding_binary_roundtrip(
    tmp_path: Path,
    node_command: list[str],
    linked: bool,
    binding: str | None,
    with_kwargs: bool,
) -> None:
    cls = {"op": "class", "name": "C", "body": []}
    if binding is not None:
        cls["binding"] = binding
    if with_kwargs:
        cls["kwargs"] = [{"n": "metaclass", "v": {"op": "name", "id": "type"}}]
    bundle = {
        "diaspore": 1,
        "main": "m",
        "modules": {"m": {"name": "m", "file": "m", "body": [cls]}},
    }
    if linked:
        bundle["stdlib"] = "sha256:" + "0" * 64
    fixture = tmp_path / "binding.json"
    fixture.write_text(
        json.dumps({"bundle": bundle, "encoded": encode(bundle).hex()}),
        encoding="utf-8",
    )
    script = (
        'import assert from "node:assert/strict";\n'
        'import {readFileSync} from "node:fs";\n'
        f"import {{dspbEncode, dspbDecode}} from {json.dumps((TS_DIR / 'src/dspb.ts').as_uri())};\n"
        'const {bundle, encoded} = JSON.parse(readFileSync(process.argv[1], "utf8"));\n'
        "const binary = dspbEncode(bundle);\n"
        'assert.equal(binary.toString("hex"), encoded);\n'
        "assert.deepEqual(dspbDecode(binary), bundle);\n"
        'console.log(binary.toString("hex"));\n'
    )
    process = subprocess.run(
        [*node_command, "--input-type=module", "-e", script, str(fixture)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert process.returncode == 0, process.stderr
    assert decode(bytes.fromhex(process.stdout.strip())) == bundle


def test_ts_class_binding_preserves_legacy_bytes_and_rejects_unknown_flags(
    node_command: list[str],
) -> None:
    script = (
        'import assert from "node:assert/strict";\n'
        f"import {{dspbEncode, dspbDecode}} from {json.dumps((TS_DIR / 'src/dspb.ts').as_uri())};\n"
        f"import {{Interp}} from {json.dumps((TS_DIR / 'src/interp.ts').as_uri())};\n"
        r"""
const bundle = {diaspore:1, main:"m", modules:{m:{name:"m", file:"m",
  body:[{op:"class", name:"C", body:[]}]}}};
const legacy = Buffer.concat([Buffer.from("DSPB01"),
  Buffer.from([2, 1, 109, 1, 67, 1, 0, 1, 0, 0, 0, 1, 32, 0, 1, 0, 0, 0])]);
assert.deepEqual(dspbEncode(bundle), legacy);
assert.deepEqual(dspbDecode(legacy), bundle);
for (const flag of [4, 8, 128, 255]) {
  const corrupt = Buffer.from(legacy);
  corrupt[corrupt.length - 3] = flag;
  assert.throws(() => dspbDecode(corrupt), /unknown class flags/);
}
for (const binding of [undefined, null, false, 1, []]) {
  const invalid = structuredClone(bundle);
  invalid.modules.m.body[0].binding = binding;
  assert.throws(() => dspbEncode(invalid), /invalid class binding/);
}
const renamed = structuredClone(bundle);
renamed.modules.m.body[0].binding = "_Owner__C";
for (const transported of [renamed, dspbDecode(dspbEncode(renamed))]) {
  const runtime = new Interp(transported);
  assert.equal(runtime.run(), 0);
  const namespace = runtime.modules.get("m").frame.vars;
  assert.equal(namespace.has("C"), false);
  const cls = namespace.get("_Owner__C");
  assert.equal(runtime.drive(runtime.getAttr(cls, "__name__")), "C");
  runtime.close();
}
const malformed = structuredClone(bundle);
malformed.modules.m.body[0].binding = null;
assert.throws(() => new Interp(malformed).run(), /invalid class binding/);
"""
    )
    process = subprocess.run(
        [*node_command, "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert process.returncode == 0, process.stderr


@pytest.mark.parametrize("binary", [False, True], ids=["json", "dspb"])
def test_ts_private_nested_class_keeps_original_name(
    tmp_path: Path,
    node_command: list[str],
    binary: bool,
) -> None:
    entry = tmp_path / "private_class.py"
    entry.write_text(
        "class Outer:\n"
        "    class __Inner:\n        pass\n"
        "    alias = __Inner\n"
        "    @classmethod\n"
        "    def make(cls):\n        return cls.__Inner()\n"
        "print(Outer._Outer__Inner.__name__)\n"
        "print(Outer.alias is Outer._Outer__Inner)\n"
        "print(type(Outer.make()).__name__)\n"
        "print('__Inner' in Outer.__dict__, '_Outer__Inner' in Outer.__dict__)\n",
        encoding="utf-8",
    )
    expected = run_cpython(entry)
    assert expected.code == 0, expected.err_line
    bundle = compile_bundle(str(entry), stdlib=False)
    path = tmp_path / ("private.dspb" if binary else "private.dsp.json")
    path.write_bytes(encode(bundle) if binary else json.dumps(bundle).encode("utf-8"))
    process = subprocess.run(
        [*node_command, str(TS_DIR / "src/cli.ts"), "run", str(path)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    lines = process.stderr.strip().splitlines()
    actual = Result(process.stdout, process.returncode, lines[-1] if lines else "")
    assert_matches(actual, expected, "TypeScript private class binding")
