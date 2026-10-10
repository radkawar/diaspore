"""Lua compact stores decode only imported modules, without a whole-store IR heap."""

from __future__ import annotations

import hashlib
import json
import shutil
import struct
import subprocess
import zlib
from pathlib import Path

import pytest

from diaspore.dspb import OPS, decode, encode
from diaspore.stdlib_store_codec import encode_stdlib_store

ROOT = Path(__file__).resolve().parents[1]
LUA = ROOT / "runtimes" / "lua"


def _store(modules: dict) -> dict:
    store = {"diaspore_stdlib": 1, "modules": modules}
    canonical = json.dumps(store, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return {**store, "id": "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()}


def _module(name: str, value: int = 1) -> dict:
    return {
        "name": name,
        "file": f"<stdlib>/{name.replace('.', '/')}.py",
        "body": [{"op": "assign", "targets": [{"op": "name", "id": "value"}],
                  "v": {"op": "const", "t": "int", "v": str(value)}}],
    }


def _install(tmp_path: Path, data: bytes) -> Path:
    runtime = tmp_path / "runtime"
    (runtime / "diaspore").mkdir(parents=True)
    shutil.copyfile(LUA / "diaspore" / "stdlib.lua", runtime / "diaspore" / "stdlib.lua")
    target = runtime / "stdlib.dsps"
    target.write_bytes(data)
    return target


def _lua(tmp_path: Path, script: str, *args: Path | str) -> str:
    lua = shutil.which("lua5.4") or shutil.which("lua")
    if lua is None:
        pytest.skip("Lua 5.4 not installed")
    paths = ";".join((str(tmp_path / "runtime" / "?.lua"), str(LUA / "?.lua"),
                      str(LUA / "?" / "init.lua")))
    runner = tmp_path / "probe.lua"
    runner.write_text(f"package.path = {json.dumps(paths)} .. ';' .. package.path\n" + script)
    result = subprocess.run([lua, str(runner), *map(str, args)], capture_output=True,
                            text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    return result.stdout


@pytest.mark.parametrize("dictionary", [b"", bytes(range(256)) * 128])
def test_lua_store_index_only_startup_and_isolated_cache(
    tmp_path: Path, dictionary: bytes,
) -> None:
    store = _store({"alpha": _module("alpha"), "beta": _module("beta", 2)})
    data = _with_dictionary(encode_stdlib_store(store, compress=False), dictionary)
    target = _install(tmp_path, data)
    output = _lua(tmp_path, '''
local D = require("diaspore.dspb")
local decode, calls = D.decode, 0
D.decode = function(bytes) calls = calls + 1; return decode(bytes) end
local S = require("diaspore.stdlib")
local real_open, payload_reads = io.open, 0
local f = assert(real_open(arg[1], "rb"))
local header = f:read(42); f:close()
local boundary = 42 + string.unpack("<I4", header, 7)
io.open = function(path, mode)
  local file = assert(real_open(path, mode))
  if path:sub(-11) ~= "stdlib.dsps" then return file end
  return {
    read = function(_, n)
      assert(type(n) == "number", "store must not read the entire file")
      if file:seek() >= boundary then payload_reads = payload_reads + 1 end
      return file:read(n)
    end,
    seek = function(_, ...) return file:seek(...) end,
    close = function() return file:close() end,
  }
end
local store = S.load()
assert(store.id == arg[2])
assert(S.has_module(store, "alpha") and not S.has_module(store, "missing"))
assert(S.module_file(store, "alpha") == "<stdlib>/alpha.py")
local names = S.module_names(store)
assert(table.concat(names, ",") == "alpha,beta")
names[1] = "changed"
assert(S.module_names(store)[1] == "alpha")
assert(calls == 0 and payload_reads == 0)
assert(S.module(store, "missing") == nil)
local first = S.module(store, "alpha")
first.body[1].v.v = "mutated"
assert(S.module(store, "alpha").body[1].v.v == "1")
assert(calls == 1 and payload_reads == 1)
local bridge = S.make_module(store)
local I = { fuel_n = function() end }
local V = require("diaspore.values")
local one = bridge.ns.get_module.fn(I, { "alpha" }, {})
V.dict_set(one, "name", "changed")
local two = bridge.ns.get_module.fn(I, { "alpha" }, {})
assert(V.dict_get(two, "name") == "alpha")
assert(calls == 1 and payload_reads == 1)
print("lazy isolated")
''', target, store["id"])
    assert output == "lazy isolated\n"


@pytest.mark.parametrize("dictionary", [b"", b"unused preset dictionary"])
def test_lua_store_defers_payload_integrity_until_import(
    tmp_path: Path, dictionary: bytes,
) -> None:
    store = _store({"alpha": _module("alpha"), "beta": _module("beta", 2)})
    data = bytearray(_with_dictionary(encode_stdlib_store(store, compress=False), dictionary))
    data[-1] ^= 1
    _install(tmp_path, bytes(data))
    assert _lua(tmp_path, '''
local S = require("diaspore.stdlib")
local store = S.load()
assert(#S.module_names(store) == 2)
assert(S.module(store, "alpha").name == "alpha")
for _ = 1, 2 do
  local ok, err = pcall(S.module, store, "beta")
  assert(not ok and tostring(err):find("payload digest mismatch", 1, true))
end
print("deferred integrity")
''') == "deferred integrity\n"


def _container(index: bytes, payload: bytes) -> bytes:
    return (b"DSPS02" + struct.pack("<I", len(index))
            + hashlib.sha256(index).digest() + index + payload)


def _with_dictionary(data: bytes, dictionary: bytes) -> bytes:
    assert data[:6] == b"DSPS02"
    length = struct.unpack_from("<I", data, 6)[0]
    index, payload = data[42:42 + length], data[42 + length:]
    assert index[32:34] == b"\0\0", "raw encoder must emit an empty dictionary"
    index = index[:32] + struct.pack("<H", len(dictionary)) + dictionary + index[34:]
    return _container(index, payload)


@pytest.mark.parametrize("damage, message", [
    ("hash", "index digest mismatch"),
    ("oversize", "dictionary length"),
    ("truncated", "truncated compiled stdlib dictionary"),
    ("missing_count", "truncated compiled stdlib index record"),
    ("old_magic", "container header"),
])
def test_lua_store_rejects_dictionary_corruption(
    tmp_path: Path, damage: str, message: str,
) -> None:
    dictionary = b"unused dictionary"
    index = bytes(32) + struct.pack("<H", len(dictionary)) + dictionary
    if damage == "oversize":
        index = bytes(32) + struct.pack("<H", 32769) + bytes(32769)
    elif damage == "truncated":
        index = bytes(32) + struct.pack("<H", 32768) + dictionary
    if damage != "missing_count":
        index += struct.pack("<I", 0)
    data = _container(index, b"")
    if damage == "hash":
        data = data[:76] + bytes([data[76] ^ 1]) + data[77:]
    elif damage == "old_magic":
        data = b"DSPS01" + data[6:]
    _install(tmp_path, data)
    _lua(tmp_path, '''
local ok, err = pcall(require("diaspore.stdlib").load)
assert(not ok and tostring(err):find(arg[1], 1, true), tostring(err))
''', message)


@pytest.mark.parametrize("dictionary", [b"", b"alpha preset dictionary"])
def test_lua_store_rejects_raw_deflate(tmp_path: Path, dictionary: bytes) -> None:
    module = _module("alpha")
    payload = encode({"diaspore": 1, "main": "alpha", "modules": {"alpha": module}})
    compressor = (zlib.compressobj(wbits=-15, zdict=dictionary) if dictionary
                  else zlib.compressobj(wbits=-15))
    compressed = compressor.compress(payload) + compressor.flush()
    index = (bytes(32) + struct.pack("<H", len(dictionary)) + dictionary
             + struct.pack("<I", 1)
             + _record(b"alpha", module["file"].encode(), compressed,
                       codec=1, decoded=len(payload)))
    _install(tmp_path, _container(index, compressed))
    _lua(tmp_path, '''
local ok, err = pcall(require("diaspore.stdlib").load)
assert(not ok and tostring(err):find("unsupported compiled stdlib codec 1", 1, true))
''')


@pytest.mark.parametrize("dictionary", [b"", b"unused dictionary"])
def test_lua_store_empty_modules(tmp_path: Path, dictionary: bytes) -> None:
    store = _store({})
    _install(tmp_path, _with_dictionary(encode_stdlib_store(store, compress=False), dictionary))
    _lua(tmp_path, '''
local S = require("diaspore.stdlib")
local store = S.load()
assert(store.id == arg[1] and #S.module_names(store) == 0)
assert(not S.has_module(store, "alpha") and S.module(store, "alpha") == nil)
''', store["id"])


def _record(name: bytes, path: bytes, payload: bytes, *, offset: int = 0,
            codec: int = 0, decoded: int | None = None) -> bytes:
    decoded_length = len(payload) if decoded is None else decoded
    return (struct.pack("<H", len(name)) + name + struct.pack("<H", len(path)) + path
            + struct.pack("<III", offset, len(payload), decoded_length)
            + bytes([codec]) + hashlib.sha256(payload).digest())


@pytest.mark.parametrize("damage", ["index_hash", "truncated", "trailer", "index_trailer",
                                    "gap", "length", "codec", "duplicate", "unsorted",
                                    "name", "utf8", "file", "version", "module", "body"])
def test_lua_store_rejects_corruption(tmp_path: Path, damage: str) -> None:
    module = _module("alpha")
    payload = encode({"diaspore": 1, "main": "alpha", "modules": {"alpha": module}})
    name, path, codec, offset, decoded = b"alpha", module["file"].encode(), 0, 0, len(payload)
    lazy = damage in {"version", "module", "body"}
    if damage == "name":
        name = b"bad..name"
    elif damage == "utf8":
        name = b"\xff"
    elif damage == "file":
        path = b"bad\x00path"
    elif damage == "codec":
        codec = 1
    elif damage == "gap":
        offset = 1
    elif damage == "length":
        decoded += 1
    elif damage == "version":
        payload = b"DSPB02" + payload[6:]
    elif damage == "module":
        path = b"different.py"
    elif damage == "body":
        broken = {**module, "body": [{"op": "boolop", "kind": "and", "values": []}]}
        payload = encode({"diaspore": 1, "main": "alpha", "modules": {"alpha": broken}})
        decoded = len(payload)
    record = _record(name, path, payload, offset=offset, codec=codec, decoded=decoded)
    count = 1
    if damage in {"duplicate", "unsorted"}:
        count = 2
        second = name if damage == "duplicate" else b"aardvark"
        record += _record(second, path, payload, offset=len(payload))
        payload += payload
    index = bytes(32) + struct.pack("<HI", 0, count) + record
    if damage == "index_trailer":
        index += b"x"
    data = _container(index, payload)
    if damage == "index_hash":
        data = data[:10] + bytes([data[10] ^ 1]) + data[11:]
    elif damage == "truncated":
        data = data[:-1]
    elif damage == "trailer":
        data += b"x"
    _install(tmp_path, data)
    _lua(tmp_path, '''
local S = require("diaspore.stdlib")
local ok, result = pcall(S.load)
if arg[1] == "lazy" then
  assert(ok, result)
  ok, result = pcall(S.module, result, "alpha")
end
assert(not ok, "corrupt container was accepted")
assert(tostring(result):find("stdlib", 1, true) or tostring(result):find("DSPB", 1, true))
''', "lazy" if lazy else "index")


def test_lua_store_json_override_hash_and_isolation(tmp_path: Path) -> None:
    store = _store({"alpha": _module("alpha")})
    target = tmp_path / "override.json"
    target.write_text(json.dumps(store))
    _lua(tmp_path, '''
local S, J = require("diaspore.stdlib"), require("diaspore.json")
local file = assert(io.open(arg[1], "rb"))
local raw = J.parse(file:read("a")); file:close()
local store = S.load(raw)
raw.modules.alpha.body[1].v.v = "7"
assert(S.module(store, "alpha").body[1].v.v == "1")
local ok, err = pcall(S.load, raw)
assert(not ok and tostring(err):find("content id mismatch", 1, true))
assert(S.load(store) == store)
ok = pcall(function() store.id = "changed" end)
assert(not ok)
''', target)


def test_lua_store_does_not_fall_back_to_json(tmp_path: Path) -> None:
    target = _install(tmp_path, b"")
    target.unlink()
    target.with_suffix(".json").write_text(json.dumps(_store({"alpha": _module("alpha")})))
    _lua(tmp_path, '''
local ok, err = pcall(require("diaspore.stdlib").load)
assert(not ok and tostring(err):find("stdlib.dsps", 1, true))
''')


def test_lua_linked_vm_imports_are_private_and_user_modules_win(tmp_path: Path) -> None:
    store = _store({"alpha": _module("alpha")})
    _install(tmp_path, encode_stdlib_store(store, compress=False))
    _lua(tmp_path, '''
local D, S = require("diaspore.dspb"), require("diaspore.stdlib")
local decode, calls = D.decode, 0
D.decode = function(bytes) calls = calls + 1; return decode(bytes) end
local function bundle(linked, user)
  return { diaspore = 1, main = "main", stdlib = linked and S.load().id or nil,
    modules = { main = { name = "main", file = "main.py", body = {} }, alpha = user } }
end
local new = require("diaspore.interp").new
local first, second = new(bundle(true), {}), new(bundle(true), {})
assert(calls == 0 and next(first.linked_ir) == nil and first.modules.alpha == nil)
local a = first:import_module("alpha")
assert(a.frame.vars.value == 1 and calls == 1)
a.frame.vars.value = 9
first.linked_ir.alpha.body[1].v.v = "9"
assert(first:import_module("alpha") == a)
assert(second:import_module("alpha").frame.vars.value == 1 and calls == 1)
assert(first.linked_ir.alpha ~= second.linked_ir.alpha)
local user = { name = "alpha", file = "user.py", body = {} }
local shadow = new(bundle(true, user), {})
assert(shadow:import_module("alpha").frame.vars.__file__ == "user.py")
assert(shadow.linked_ir.alpha == nil and calls == 1)
local standalone = new(bundle(false), {})
local ok = pcall(standalone.import_module, standalone, "alpha")
assert(not ok and standalone.stdlib == nil)
local mismatch = bundle(true)
mismatch.stdlib = "sha256:" .. string.rep("0", 64)
local err
ok, err = pcall(new, mismatch, {})
assert(not ok and tostring(err):find("stdlib id mismatch", 1, true))
''')


def _all_nodes() -> list[dict]:
    name = {"op": "name", "id": "x"}
    const = {"op": "const", "t": "int", "v": "1"}
    nodes = [
        const, name, {"op": "attr", "o": name, "n": "y"},
        {"op": "index", "o": name, "k": const},
        {"op": "slicelit", "lower": const, "upper": const, "step": const},
        {"op": "starred", "v": name},
        {"op": "call", "f": name, "args": [const], "kw": [{"n": None, "v": name}]},
        {"op": "binop", "kind": "matmul", "l": name, "r": name},
        {"op": "unary", "kind": "invert", "v": const},
        {"op": "boolop", "kind": "or", "values": [name, const]},
        {"op": "compare", "left": name, "ops": ["isnot"], "comps": [const]},
        {"op": "cond", "test": name, "then": const, "else": name},
        *({"op": op, "elts": [name, const]} for op in ("list", "tuple", "set")),
        {"op": "dict", "entries": [{"k": name, "v": const}, {"k": None, "v": name}]},
        {"op": "fstring", "parts": [{"s": "\ud800\udfff", "codepoints": [0xD800, 0xDFFF]},
                                    {"s": "\udfff", "codepoints": [0xDFFF]},
                                    {"v": name, "conv": "a", "spec": const}]},
        *({"op": op, "name": "function", "gen": True, "coro": True, "asyncgen": True,
           "annotation_scope": True, "params": {"args": ["a"], "posonly": 1,
           "defaults": [const], "vararg": "args", "kwonly": ["b", "c"],
           "kwdefaults": [None, const], "kwarg": "kw"}, "locals": ["a"],
           "gdecl": ["global"], "ndecl": ["nonlocal"], "body": []}
          for op in ("func", "funcexpr")),
        *({"op": op, "v": const} for op in ("yield", "yieldfrom", "expr", "return")),
        {"op": "namedexpr", "target": name, "v": const},
        {"op": "assign", "targets": [name], "v": const},
        {"op": "augassign", "target": name, "kind": "add", "v": const},
        {"op": "if", "test": name, "then": [], "orelse": [{"op": "pass"}]},
        {"op": "while", "test": name, "body": [], "orelse": [{"op": "pass"}]},
        {"op": "for", "target": name, "iter": name, "body": [], "orelse": [{"op": "pass"}]},
        *({"op": op} for op in ("break", "continue", "pass")),
        {"op": "class", "name": "__Inner", "binding": "_Outer__Inner", "bases": [name],
         "kwargs": [{"n": "metaclass", "v": name}], "body": []},
        {"op": "raise", "exc": name, "cause": const},
        {"op": "try", "star": True, "body": [], "handlers": [{"t": name, "name": "e", "body": []}],
         "orelse": [{"op": "pass"}], "finalbody": [{"op": "pass"}]},
        {"op": "with", "items": [{"ctx": name, "name": name}], "body": []},
        {"op": "import", "module": "x", "bind": "root", "alias": "y"},
        {"op": "importfrom", "module": "x", "names": [{"n": "a", "alias": "b"}]},
        {"op": "del", "targets": [name]},
        *({"op": "const", "t": kind, "v": value} for kind, value in
          [("str", "\ud800"), ("bytes", "00ff"), ("float", "1.5"), ("bool", False)]),
        {"op": "const", "t": "complex", "re": "1.0", "im": "-2.0"},
        {"op": "const", "t": "none"}, {"op": "const", "t": "ellipsis"},
    ]
    assert {node["op"] for node in nodes} == set(OPS)
    for node in nodes:
        node["ln"] = 42
    return nodes


def test_lua_dspb_full_compact_model(tmp_path: Path) -> None:
    module = {"name": "m", "file": "m.py", "body": _all_nodes(), "src": ["source"]}
    payload = encode({"diaspore": 1, "main": "m", "modules": {"m": module}})
    target = tmp_path / "module.dspb"
    target.write_bytes(payload)
    output = _lua(tmp_path, '''
local file = assert(io.open(arg[1], "rb"))
local bytes = file:read("a"); file:close()
local bundle = require("diaspore.dspb").decode(bytes)
print(require("diaspore.json").stringify_raw(bundle))
''', target)
    assert json.loads(output) == decode(payload)


@pytest.mark.parametrize("payload", [
    b"DSPB02", b"DSPB01\x80", b"DSPB01" + b"\xff" * 10,
    b"DSPB01\x01\x01\xff", b"DSPB01\x01\x04\xf4\x90\x80\x80",
    b"DSPB01\x01\x01m\x01\x01\x00",
    b"DSPB01\x01\x01m\x01\x00\x01\x00\x00\x00\x01\xff\x00",
    b"DSPB01\x01\x01m\x01\x00\x01\x00\x00\x00\x00x",
])
def test_lua_dspb_rejects_malformed_payload(tmp_path: Path, payload: bytes) -> None:
    target = tmp_path / "invalid.dspb"
    target.write_bytes(payload)
    _lua(tmp_path, '''
local file = assert(io.open(arg[1], "rb"))
local bytes = file:read("a"); file:close()
local ok, err = pcall(require("diaspore.dspb").decode, bytes)
assert(not ok and tostring(err):find("invalid DSPB", 1, true))
''', target)
