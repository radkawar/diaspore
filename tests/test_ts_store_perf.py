"""Compact TypeScript store integrity, lazy decoding, and string join regressions."""

import hashlib
import json
import subprocess
import zlib
from pathlib import Path

import pytest

from diaspore.stdlib_store_codec import encode_stdlib_store
from diaspore.vm import bundle_digest

from .conftest import TS_DIR


@pytest.fixture(params=["raw", "dictionary", "empty-dictionary"])
def compact_store(tmp_path: Path, request) -> Path:
    modules = {
        name: {"name": name, "file": f"<stdlib>/{name}.py", "body": []}
        for name in ["alpha", "omega"]
    }
    store = {"diaspore_stdlib": 1, "modules": modules}
    store["id"] = "sha256:" + bundle_digest(store)
    path = tmp_path / "stdlib.dsps"
    data = encode_stdlib_store(store, compress=request.param == "dictionary")
    if request.param == "empty-dictionary":
        # Reframe raw module payloads as ordinary raw DEFLATE, without training.
        index_length = int.from_bytes(data[6:10], "little")
        index = bytearray(data[42 : 42 + index_length])
        assert index[32:34] == b"\0\0"
        cursor, offset = 38, 0
        payloads = []
        for _ in modules:
            for _ in range(2):
                length = int.from_bytes(index[cursor : cursor + 2], "little")
                cursor += 2 + length
            start = int.from_bytes(index[cursor : cursor + 4], "little")
            length = int.from_bytes(index[cursor + 4 : cursor + 8], "little")
            decoded = data[42 + index_length + start : 42 + index_length + start + length]
            compressor = zlib.compressobj(wbits=-15)
            payload = compressor.compress(decoded) + compressor.flush()
            index[cursor : cursor + 4] = offset.to_bytes(4, "little")
            index[cursor + 4 : cursor + 8] = len(payload).to_bytes(4, "little")
            index[cursor + 12] = 1
            index[cursor + 13 : cursor + 45] = hashlib.sha256(payload).digest()
            payloads.append(payload)
            offset += len(payload)
            cursor += 45
        data = data[:10] + hashlib.sha256(index).digest() + index + b"".join(payloads)
    else:
        dictionary_length = int.from_bytes(data[74:76], "little")
        assert bool(dictionary_length) == (request.param == "dictionary")
    path.write_bytes(data)
    return path


def run_ts(ts_vm: list[str], path: Path, checks: str) -> None:
    imports = (
        'import assert from "node:assert/strict";\n'
        'import {readFileSync} from "node:fs";\n'
        'import {createHash} from "node:crypto";\n'
        'import {deflateRawSync, gzipSync} from "node:zlib";\n'
        "import {decodeStdlibStore, validateStdlibStore} from "
        f"{json.dumps((TS_DIR / 'src/stdlib.ts').as_uri())};\n"
        f"import {{dspbEncode}} from {json.dumps((TS_DIR / 'src/dspb.ts').as_uri())};\n"
        f"import {{Interp}} from {json.dumps((TS_DIR / 'src/interp.ts').as_uri())};\n"
        'const bytes = readFileSync(process.argv[1]);\n'
    )
    helpers = """
const indexLength = bytes.readUInt32LE(6), payloadStart = 42 + indexLength;
const records = [];
const dictionaryLength = bytes.readUInt16LE(74);
const dictionary = bytes.subarray(76, 76 + dictionaryLength);
const countPosition = 76 + dictionaryLength;
let cursor = countPosition + 4;
for (let i = 0; i < bytes.readUInt32LE(countPosition); i++) {
  const name = cursor + 2;
  cursor += 2 + bytes.readUInt16LE(cursor);
  const file = cursor + 2;
  cursor += 2 + bytes.readUInt16LE(cursor);
  records.push({name, file, fields:cursor});
  cursor += 45;
}
function rehashIndex(value) {
  createHash('sha256').update(value.subarray(42, payloadStart)).digest().copy(value, 10);
  return value;
}
function replaceLast(decoded) {
  const value = Buffer.from(bytes), at = records[1].fields;
  const offset = value.readUInt32LE(at), codec = value[at + 12];
  const stored = codec === 1 ? deflateRawSync(decoded, {dictionary}) : decoded;
  value.writeUInt32LE(stored.length, at + 4);
  value.writeUInt32LE(decoded.length, at + 8);
  createHash('sha256').update(stored).digest().copy(value, at + 13);
  return Buffer.concat([rehashIndex(value).subarray(0, payloadStart + offset), stored]);
}
"""
    result = subprocess.run(
        [ts_vm[0], "--input-type=module", "-e", imports + helpers + checks, str(path)],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr


def test_ts_compact_index_is_lazy_and_ir_is_private(ts_vm, compact_store):
    run_ts(
        ts_vm,
        compact_store,
        """
const corrupted = Buffer.from(bytes);
corrupted[corrupted.length - 1] ^= 1;
const store = decodeStdlibStore(corrupted);
assert.deepEqual(Object.keys(store.modules), ['alpha', 'omega']);
assert.equal(Object.hasOwn(store.modules, 'omega'), true);
assert.equal(validateStdlibStore(store), store);
assert.ok(Object.isFrozen(store));
assert.ok(Object.isFrozen(store.modules));
const bundle = {diaspore:1, main:'app', stdlib:store.id,
  modules:{app:{name:'app', file:'app.py', body:[]}}};
const vm = new Interp(bundle, {stdlibStore:store});
assert.equal(vm.run(), 0);
assert.equal(vm.modules.has('omega'), false);
const first = store.modules.alpha;
assert.equal(store.modules.alpha, first);
assert.ok(Object.isFrozen(first.body));
assert.throws(() => first.body.push({op:'pass'}), TypeError);
assert.throws(() => store.modules.omega, /payload digest mismatch/);
assert.throws(() => store.modules.omega, /payload digest mismatch/);
const pristine = Buffer.from(bytes), isolated = decodeStdlibStore(pristine);
pristine.fill(0);
assert.equal(isolated.modules.omega.name, 'omega');
assert.equal(isolated.id, store.id);
""",
    )


def test_ts_compact_index_rejects_corruption_before_decode(ts_vm, compact_store):
    run_ts(
        ts_vm,
        compact_store,
        """
for (const position of [0, 10, 42]) {
  const bad = Buffer.from(bytes); bad[position] ^= 1;
  assert.throws(() => decodeStdlibStore(bad));
}
const highMagic = Buffer.from(bytes); highMagic[0] |= 128;
assert.throws(() => decodeStdlibStore(highMagic));
const legacy = Buffer.from(bytes); legacy.write('DSPS01');
assert.throws(() => decodeStdlibStore(legacy), /invalid DSPS02 header/);
if (dictionaryLength) {
  const bad = Buffer.from(bytes); bad[76] ^= 1;
  assert.throws(() => decodeStdlibStore(bad), /index digest mismatch/);
}
assert.throws(() => decodeStdlibStore(bytes.subarray(0, bytes.length - 1)));
assert.throws(() => decodeStdlibStore(Buffer.concat([bytes, Buffer.from([0])])));
for (const change of [
  b => b.writeUInt32LE(0xffffffff, countPosition),
  b => b.writeUInt16LE(32769, 74),
  b => b.writeUInt16LE(Math.min(32768, indexLength), 74),
  b => b.writeUInt32LE(1, records[0].fields),
  b => b.writeUInt32LE(0, records[0].fields + 4),
  b => b.writeUInt32LE(0xffffffff, records[0].fields + 8),
  b => { b[records[0].fields + 12] = 2; },
  b => { b[records[0].name] = 0xff; },
  b => { b[records[0].name] = 46; },
  b => { b[records[0].file] = 0; },
  b => { b.write('alpha', records[1].name); },
]) {
  const bad = Buffer.from(bytes); change(bad); rehashIndex(bad);
  assert.throws(() => decodeStdlibStore(bad));
}
""",
    )


def test_ts_compact_payload_identity_checked_on_first_access(ts_vm, compact_store):
    run_ts(
        ts_vm,
        compact_store,
        """
const module = {name:'omega', file:'<stdlib>/omega.py', body:[]};
const bundle = {diaspore:1, main:'omega', modules:{omega:module}};
for (const malformed of [
  {...bundle, main:'alpha'},
  {...bundle, stdlib:'sha256:' + '0'.repeat(64)},
  {...bundle, modules:{omega:{...module, file:'wrong.py'}}},
  {...bundle, modules:{omega:module, extra:{...module, name:'extra'}}},
  {...bundle, modules:{other:{...module, name:'other'}}},
]) {
  const store = decodeStdlibStore(replaceLast(dspbEncode(malformed)));
  assert.deepEqual(Object.keys(store.modules), ['alpha', 'omega']);
  assert.equal(store.modules.alpha.name, 'alpha');
  assert.throws(() => store.modules.omega);
}
const truncated = dspbEncode(bundle).subarray(0, -1);
const store = decodeStdlibStore(replaceLast(truncated));
assert.throws(() => store.modules.omega);
""",
    )


def test_ts_raw_deflate_integrity_and_output_bound(ts_vm, compact_store):
    run_ts(
        ts_vm,
        compact_store,
        """
if (bytes[records[1].fields + 12] === 1) {
  const at = records[1].fields;
  const decoded = dspbEncode({diaspore:1, main:'omega', modules:{
    omega:{name:'omega', file:'<stdlib>/omega.py', body:[]}}});
  // Both malformed raw DEFLATE and the obsolete gzip codec must fail lazily,
  // even when the container's payload and index digests are self-consistent.
  for (const stored of [Buffer.from([7]), gzipSync(decoded)]) {
    const bad = Buffer.from(bytes), offset = bad.readUInt32LE(at);
    bad.writeUInt32LE(stored.length, at + 4);
    createHash('sha256').update(stored).digest().copy(bad, at + 13);
    const store = decodeStdlibStore(Buffer.concat([
      rehashIndex(bad).subarray(0, payloadStart + offset), stored]));
    assert.equal(store.modules.alpha.name, 'alpha');
    assert.throws(() => store.modules.omega);
    assert.throws(() => store.modules.omega);
  }
  const bad = replaceLast(decoded);
  bad.writeUInt32LE(6, at + 8);
  const bounded = decodeStdlibStore(rehashIndex(bad));
  assert.equal(bounded.modules.alpha.name, 'alpha');
  assert.throws(() => bounded.modules.omega);
}
""",
    )


def test_ts_dictionary_is_used_and_corruption_is_authenticated(ts_vm, compact_store):
    run_ts(
        ts_vm,
        compact_store,
        """
const decoded = dspbEncode({diaspore:1, main:'omega', modules:{
  omega:{name:'omega', file:'<stdlib>/omega.py', body:[]}}});
// The full payload preset forces a reference regardless of training choices.
const preset = decoded;
const stored = deflateRawSync(decoded, {dictionary:preset, level:9});
const record = Buffer.from(bytes.subarray(records[1].name - 2, payloadStart));
const fields = records[1].fields - (records[1].name - 2);
record.writeUInt32LE(0, fields);
record.writeUInt32LE(stored.length, fields + 4);
record.writeUInt32LE(decoded.length, fields + 8);
record[fields + 12] = 1;
createHash('sha256').update(stored).digest().copy(record, fields + 13);
const prefix = Buffer.alloc(34);
bytes.copy(prefix, 0, 42, 74);
prefix.writeUInt16LE(preset.length, 32);
const count = Buffer.alloc(4); count.writeUInt32LE(1);
const index = Buffer.concat([prefix, preset, count, record]);
const header = Buffer.alloc(42); header.write('DSPS02');
header.writeUInt32LE(index.length, 6);
createHash('sha256').update(index).digest().copy(header, 10);
const good = Buffer.concat([header, index, stored]);
assert.equal(decodeStdlibStore(good).modules.omega.name, 'omega');
const bad = Buffer.from(good);
bad.fill(0, 76, 76 + preset.length);
assert.throws(() => decodeStdlibStore(bad), /index digest mismatch/);
// Reauthenticated wrong dictionary content must still fail at first use.
createHash('sha256').update(bad.subarray(42, 42 + index.length)).digest().copy(bad, 10);
const lazy = decodeStdlibStore(bad);
assert.deepEqual(Object.keys(lazy.modules), ['omega']);
assert.throws(() => lazy.modules.omega);
assert.throws(() => lazy.modules.omega);
""",
    )


def test_ts_large_join_preserves_surrogate_boundaries(tmp_path, ts_vm):
    script = (
        'import assert from "node:assert/strict";\n'
        "import {strJoin, chars, PyStr} from "
        f"{json.dumps((TS_DIR / 'src/values.ts').as_uri())};\n"
        r"""
const parts = Array.from({length:30000}, (_, i) => `item-${i}`);
const joined = strJoin(parts, ',');
assert.equal(joined.length, 318889);
assert.equal(joined, parts.join(','));
for (const [parts, separator, expected] of [
  [['\ud800', '', '\udc00'], '', ['\ud800', '\udc00']],
  [['\ud800', '\udc00'], '', ['\ud800', '\udc00']],
  [['\ud800', '\udc00'], ',', ['\ud800', ',', '\udc00']],
  [['', '\udc00'], '\ud800', ['\ud800', '\udc00']],
  [['\ud800', ''], '\udc00', ['\ud800', '\udc00']],
  [['\ud800\udc00', 'x'], '', ['\ud800\udc00', 'x']],
  [[new PyStr(['\ud800', '\udc00']), 'x'], '', ['\ud800', '\udc00', 'x']],
]) assert.deepEqual(chars(strJoin(parts, separator)), expected);
"""
    )
    result = subprocess.run(
        [ts_vm[0], "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
