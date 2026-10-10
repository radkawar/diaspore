"""Exercise compact-store laziness and the Python VM's protocol-safe fast paths."""

import hashlib
import io
import shutil
import struct
import subprocess
import sys
import tarfile
import zipfile
import zlib
from pathlib import Path

import pytest

from diaspore import compiler, dspb, stdlib_store
from diaspore.stdlib_store_codec import (
    StoreError,
    _dictionary,
    decode_stdlib_store,
    encode_stdlib_store,
    load_stdlib_store,
)
from diaspore.validator import BundleValidationError
from diaspore.vm import Interp


def _store(sources=None):
    sources = sources or {"sample": "VALUE = 7\n", "unused": "VALUE = 8\n"}
    modules = {}
    for name, source in sources.items():
        module = compiler.ModuleCompiler(name, "<stdlib>/" + name + ".py", source).compile()
        module["src"] = source.splitlines()
        modules[name] = module
    compiler.compact_bundle({"modules": modules})
    store = {"diaspore_stdlib": 1, "modules": modules}
    store["id"] = "sha256:" + hashlib.sha256(stdlib_store._canonical(store)).hexdigest()
    return store


def _container(records, payloads, *, preset=b""):
    index = bytearray(b"\0" * 32 + struct.pack("<H", len(preset)) + preset
                      + struct.pack("<I", len(records)))
    for name, filename, offset, stored, decoded, codec, digest in records:
        for text in (name, filename):
            raw = text if isinstance(text, bytes) else text.encode()
            index.extend(struct.pack("<H", len(raw)) + raw)
        index.extend(struct.pack("<IIIB32s", offset, stored, decoded, codec, digest))
    return (b"DSPS02" + struct.pack("<I", len(index)) + hashlib.sha256(index).digest()
            + index + payloads)


def _one_payload(bundle, *, name="sample", filename="<stdlib>/sample.py", codec=0,
                 decoded_length=None, payload=None, preset=b""):
    encoded = dspb.encode(bundle)
    payload = encoded if payload is None else payload
    return _container([(name, filename, 0, len(payload),
                        len(encoded) if decoded_length is None else decoded_length,
                        codec, hashlib.sha256(payload).digest())], payload, preset=preset)


def _deflate(payload, preset=b""):
    compressor = (zlib.compressobj(level=9, wbits=-15, zdict=preset) if preset
                  else zlib.compressobj(level=9, wbits=-15))
    return compressor.compress(payload) + compressor.flush()


def test_dictionary_training_counts_modules_and_orders_deterministically():
    assert _dictionary({"one": b"abcdefgh" * 100}) == b""
    assert _dictionary({"one": b"abcdefgh", "two": b"abcdefgh"}) == b"abcdefgh"
    payloads = {"one": bytes(range(256)) * 600, "two": bytes(range(256)) * 700}
    preset = _dictionary(payloads)
    assert 0 < len(preset) <= 32768
    assert preset == _dictionary(dict(reversed(list(payloads.items()))))


@pytest.mark.parametrize("compress", [False, True])
def test_encoder_dictionary_header_and_deterministic_payloads(compress):
    original = _store()
    data = encode_stdlib_store(original, compress=compress)
    reordered = {**original, "modules": dict(reversed(list(original["modules"].items())))}
    assert data == encode_stdlib_store(reordered, compress=compress)
    assert data[:6] == b"DSPS02"
    length = struct.unpack_from("<I", data, 6)[0]
    index = data[42:42 + length]
    assert hashlib.sha256(index).digest() == data[10:42]
    assert index[:32].hex() == original["id"][7:]
    dictionary_length = struct.unpack_from("<H", index, 32)[0]
    assert (0 < dictionary_length <= 32768) if compress else dictionary_length == 0
    assert struct.unpack_from("<I", index, 34 + dictionary_length)[0] == 2
    modules = decode_stdlib_store(data)["modules"]
    assert dict(modules) == original["modules"]


@pytest.mark.parametrize("compress", [False, True])
def test_single_module_and_empty_store_allow_empty_dictionary(compress):
    original = _store({"sample": "VALUE = 1\n"})
    for modules in (original["modules"], {}):
        store = {"diaspore_stdlib": 1, "modules": modules}
        store["id"] = "sha256:" + hashlib.sha256(stdlib_store._canonical(store)).hexdigest()
        data = encode_stdlib_store(store, compress=compress)
        assert struct.unpack_from("<H", data, 74)[0] == 0
        loaded = decode_stdlib_store(data)
        assert loaded["id"] == store["id"]
        assert dict(loaded["modules"]) == modules


@pytest.mark.parametrize("change", ["legacy", "digest", "oversize", "truncated"])
def test_dictionary_index_rejects_corruption(change):
    data = bytearray(_container([], b"", preset=b"dictionary"))
    if change == "legacy":
        data[:6] = b"DSPS01"
    elif change == "digest":
        data[76] ^= 1
    else:
        struct.pack_into("<H", data, 74, 32769 if change == "oversize" else 11)
        data[10:42] = hashlib.sha256(data[42:]).digest()
    with pytest.raises(StoreError, match={
        "legacy": "DSPS02", "digest": "index digest",
        "oversize": "dictionary length", "truncated": "dictionary length",
    }[change]):
        decode_stdlib_store(data)


def test_maximum_dictionary_and_raw_payload_ignore_preset():
    module = _store({"sample": "VALUE = 1\n"})["modules"]["sample"]
    bundle = {"diaspore": 1, "main": "sample", "modules": {"sample": module}}
    preset = bytes(range(256)) * 128
    encoded = dspb.encode(bundle)
    for codec in (0, 1):
        payload = _deflate(encoded, preset) if codec else encoded
        data = _one_payload(bundle, codec=codec, payload=payload, preset=preset)
        assert decode_stdlib_store(data)["modules"]["sample"] == module


def test_missing_dictionary_fails_lazily_without_caching():
    module = _store({"sample": "VALUE = 1\n"})["modules"]["sample"]
    bundle = {"diaspore": 1, "main": "sample", "modules": {"sample": module}}
    encoded = dspb.encode(bundle)
    payload = _deflate(encoded, encoded)
    modules = decode_stdlib_store(_one_payload(bundle, codec=1, payload=payload))["modules"]
    assert modules._cache == {}
    for _ in range(2):
        with pytest.raises(StoreError, match="deflate payload"):
            modules["sample"]
    assert modules._cache == {}


@pytest.mark.parametrize("compress", [False, True])
def test_decode_once_with_private_ir_cache(monkeypatch, compress):
    original = _store()
    data = encode_stdlib_store(original, compress=compress)
    calls = []
    decode = dspb.decode

    def counted(payload):
        calls.append(payload)
        return decode(payload)

    monkeypatch.setattr(dspb, "decode", counted)
    loaded = decode_stdlib_store(data)
    modules = loaded["modules"]
    assert loaded["id"] == original["id"]
    assert list(modules) == ["sample", "unused"]
    assert "sample" in modules and "missing" not in modules
    assert modules.module_file("sample") == "<stdlib>/sample.py"
    assert calls == [] and modules._cache == {}
    first = modules["sample"]
    assert first == original["modules"]["sample"]
    first["body"].clear()
    first["src"].clear()
    assert modules["sample"] == original["modules"]["sample"]
    assert len(calls) == 1
    assert set(modules._cache) == {"sample"}
    assert modules.get("absent") is None


def test_file_startup_reads_once_and_decodes_only_on_access(tmp_path, monkeypatch):
    data = encode_stdlib_store(_store())
    path = tmp_path / "stdlib.dsps"
    path.write_bytes(data)
    reads = []
    real_open = open

    class TrackedFile:
        def __init__(self, *args, **kwargs):
            self.file = real_open(*args, **kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.file.close()

        def read(self, size=-1):
            reads.append((self.file.tell(), size))
            return self.file.read(size)

    monkeypatch.setattr("builtins.open", TrackedFile)
    loaded = load_stdlib_store(path)
    assert reads == [(0, -1)]
    assert loaded["modules"]._cache == {}
    path.unlink()
    loaded["modules"]["sample"]
    assert reads == [(0, -1)]
    assert set(loaded["modules"]._cache) == {"sample"}


def test_payload_corruption_is_deferred_and_not_cached():
    data = bytearray(encode_stdlib_store(_store()))
    data[-1] ^= 1
    modules = decode_stdlib_store(data)["modules"]
    assert modules["sample"]["name"] == "sample"
    for _ in range(2):
        with pytest.raises(StoreError, match="payload digest mismatch"):
            modules["unused"]
    assert set(modules._cache) == {"sample"}


@pytest.mark.parametrize("change", ["main", "module", "file", "linked", "ir"])
def test_decoded_payload_identity_and_ir_are_validated(change):
    module = _store({"sample": "VALUE = 1\n"})["modules"]["sample"]
    bundle = {"diaspore": 1, "main": "sample", "modules": {"sample": module}}
    if change == "main":
        bundle["main"] = "other"
    elif change == "module":
        bundle["modules"]["extra"] = {"name": "extra", "file": "extra.py", "body": []}
    elif change == "file":
        module["file"] = "other.py"
    elif change == "linked":
        bundle["stdlib"] = "sha256:" + "1" * 64
    else:
        module["body"] = [{"op": "assign", "targets": [],
                           "v": {"op": "const", "t": "none"}}]
    modules = decode_stdlib_store(_one_payload(bundle))["modules"]
    with pytest.raises((StoreError, BundleValidationError)):
        modules["sample"]
    assert modules._cache == {}


def test_duplicate_dspb_module_keys_are_not_collapsed():
    payload = (b"DSPB01\x02\x06sample\x09sample.py\x01\x00\x02"
               + b"\x00\x01\x00\x00" * 2)
    data = _container([("sample", "sample.py", 0, len(payload), len(payload), 0,
                        hashlib.sha256(payload).digest())], payload)
    modules = decode_stdlib_store(data)["modules"]
    with pytest.raises(dspb.DSPBError, match="duplicate module"):
        modules["sample"]
    assert modules._cache == {}


def test_payload_digest_is_checked_before_inflation(monkeypatch):
    payload = b"\x07"
    data = _container([("sample", "sample.py", 0, 1, 6, 1, b"\0" * 32)], payload)
    modules = decode_stdlib_store(data)["modules"]

    def forbidden(*args, **kwargs):
        pytest.fail("corrupt stored bytes must not reach the inflater")

    monkeypatch.setattr(zlib, "decompressobj", forbidden)
    with pytest.raises(StoreError, match="payload digest mismatch"):
        modules["sample"]
    assert modules._cache == {}


def test_mutable_input_is_copied_to_immutable_backing():
    original = _store()
    data = bytearray(encode_stdlib_store(original))
    modules = decode_stdlib_store(data)["modules"]
    data[:] = b"\0" * len(data)
    assert modules._cache == {}
    assert dict(modules) == original["modules"]


@pytest.mark.parametrize("change", ["offset", "codec", "short", "huge", "raw_length",
                                     "zero_stored", "huge_stored", "short_decoded",
                                     "name", "utf8", "file", "duplicate", "order", "trailing"])
def test_index_rejects_invalid_records(change):
    payload = b"DSPB01"
    record = ["sample", "sample.py", 0, 6, 6, 0, hashlib.sha256(payload).digest()]
    records = [record]
    if change == "offset":
        record[2] = 1
    elif change == "codec":
        record[5] = 2
    elif change == "short":
        record[3] = 5
    elif change == "huge":
        record[4] = 0xFFFFFFFF
    elif change == "zero_stored":
        record[3], record[5] = 0, 1
    elif change == "huge_stored":
        record[3], record[5] = 0xFFFFFFFF, 1
    elif change == "short_decoded":
        record[4], record[5] = 5, 1
    elif change == "raw_length":
        record[4] = 7
    elif change == "name":
        record[0] = "bad..name"
    elif change == "utf8":
        record[0] = b"\xff"
    elif change == "file":
        record[1] = "bad\0file"
    elif change in ("duplicate", "order"):
        records.append(["sample" if change == "duplicate" else "earlier", "a.py", 6,
                        6, 6, 0, record[-1]])
        payload *= 2
    else:
        payload += b"x"
    with pytest.raises(StoreError):
        decode_stdlib_store(_container(records, payload))


@pytest.mark.parametrize("preset", [b"", b"DSPB01sample<stdlib>/sample.pyVALUE"])
@pytest.mark.parametrize("change", ["overflow", "underflow", "trailing", "truncated",
                                     "concatenated", "gzip", "zlib", "invalid"])
def test_deflate_decode_is_bounded_and_exact(change, preset):
    module = _store({"sample": "VALUE = 1\n"})["modules"]["sample"]
    bundle = {"diaspore": 1, "main": "sample", "modules": {"sample": module}}
    encoded = dspb.encode(bundle)
    payload = _deflate(encoded, preset)
    decoded_length = len(encoded)
    if change == "overflow":
        decoded_length -= 1
    elif change == "underflow":
        decoded_length += 1
    elif change == "trailing":
        payload += b"extra"
    elif change == "truncated":
        payload = payload[:-1]
    elif change == "concatenated":
        payload += _deflate(encoded, preset)
    elif change == "gzip":
        import gzip

        payload = gzip.compress(encoded, mtime=0)
    elif change == "zlib":
        payload = zlib.compress(encoded)
    else:
        payload = b"\x07"
    data = _one_payload(bundle, codec=1, payload=payload, decoded_length=decoded_length,
                        preset=preset)
    modules = decode_stdlib_store(data)["modules"]
    with pytest.raises(StoreError):
        modules["sample"]
    assert modules._cache == {}


def test_parsed_store_still_requires_canonical_content_id():
    original = _store()
    original["modules"]["sample"]["body"] = []
    with pytest.raises(ValueError, match="content id mismatch"):
        encode_stdlib_store(original)


def test_generation_does_not_reuse_installed_snapshot(tmp_path, monkeypatch):
    stale = _store({"sample": "VALUE = 1\n"})
    fresh = _store({"sample": "VALUE = 2\n"})
    monkeypatch.setattr(stdlib_store, "_store", stale)
    monkeypatch.setattr(stdlib_store, "compile_stdlib_store", lambda: fresh)
    path = tmp_path / "stdlib.dsps"
    stdlib_store.write_stdlib_store(path)
    assert load_stdlib_store(path)["id"] == fresh["id"] != stale["id"]


def test_linked_and_standalone_compilation_need_no_source_tree(tmp_path, monkeypatch):
    original = _store()
    loaded = decode_stdlib_store(encode_stdlib_store(original))
    monkeypatch.setattr(stdlib_store, "_store", loaded)
    monkeypatch.setattr(compiler, "STDLIB_DIRS", ())
    source = tmp_path / "main.py"
    source.write_text("import sample\nprint(sample.VALUE)\n")
    linked = compiler.compile_bundle(str(source))
    assert linked["stdlib"] == original["id"]
    assert loaded["modules"]._cache == {}
    standalone = compiler.compile_bundle(str(source), self_contained=True)
    assert "stdlib" not in standalone
    assert "src" not in standalone["modules"]["sample"]
    assert loaded["modules"]["sample"]["src"] == ["VALUE = 7"]
    for bundle in (linked, standalone):
        output = io.StringIO()
        machine = Interp(bundle, stdout=output)
        assert machine.run() == 0
        assert output.getvalue() == "7\n"


def _run_source(tmp_path, source):
    path = tmp_path / "main.py"
    path.write_text(source)
    output, errors = io.StringIO(), io.StringIO()
    machine = Interp(compiler.compile_bundle(str(path), stdlib=False),
                     stdout=output, stderr=errors)
    assert machine.run() == 0, errors.getvalue()
    return output.getvalue()


def test_fast_calls_preserve_mutable_function_metadata(tmp_path):
    assert _run_source(tmp_path, """
def f(a, b=2):
    return a + b
print(f(3, 4), f(3))
f.__defaults__ = (5,)
print(f(3), f(3, 6), f(a=3))
def replacement(a, b=1):
    return a * b
f.__code__ = replacement.__code__
print(f(3, 4), f(3))
def variadic(a, *args, **kwargs):
    return a, args, kwargs
print(variadic(1), variadic(1, 2, key=3))
def keyword(a, *, b=8):
    return a + b
print(keyword(2), keyword(2, b=3))
""") == "7 5\n8 9 8\n12 15\n(1, (), {}) (1, (2,), {'key': 3})\n10 5\n"


def test_primitive_fast_paths_preserve_descriptors_and_fuel(tmp_path):
    assert _run_source(tmp_path, """
class Number(int):
    def __mul__(self, other):
        return 99
class Data:
    def __get__(self, obj, owner):
        return 17
    def __set__(self, obj, value):
        obj.saved = value
class Items(list):
    count = Data()
    def append(self, value):
        self.saved = value
items = Items()
items.append(12)
items.count = 3
print(items.count, items.saved, Number(2) * 4, 2 * 4)
values = []
values.append('x')
print(values, {'x': 1}.get('x'), 'a,b'.split(','))
print(values.__class__.__name__, items.append.__self__ is items)
try:
    print(1 // 0)
except ZeroDivisionError:
    print('division')
""") == "17 3 99 8\n['x'] 1 ['a', 'b']\nlist True\ndivision\n"


def test_fast_integer_loop_keeps_step_budget(tmp_path):
    path = tmp_path / "main.py"
    path.write_text("total = 0\nfor i in range(1000):\n    total += i * i\n")
    errors = io.StringIO()
    machine = Interp(compiler.compile_bundle(str(path), stdlib=False),
                     max_steps=20, stderr=errors)
    assert machine.run() == 3
    assert "step budget exceeded (20)" in errors.getvalue()


def test_clean_source_distribution_builds_source_independent_wheel(tmp_path):
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv is required to exercise isolated wheel builds")
    root = Path(__file__).resolve().parents[1]

    def build(*arguments):
        result = subprocess.run([uv, "build", *arguments], cwd=root, capture_output=True,
                                text=True, timeout=120)
        assert result.returncode == 0, result.stdout + result.stderr

    build("--sdist", "--out-dir", str(tmp_path))
    source = next(tmp_path.glob("*.tar.gz"))
    with tarfile.open(source) as archive:
        assert not any(name.endswith("/stdlib.dsps") for name in archive.getnames())
    build(str(source), "--wheel", "--out-dir", str(tmp_path))
    installed = tmp_path / "installed"
    with zipfile.ZipFile(next(tmp_path.glob("*.whl"))) as archive:
        names = archive.namelist()
        assert names.count("diaspore/stdlib.dsps") == 1
        assert {name for name in names if name.startswith("diaspore/stdlib/")
                and name.endswith(".py")} == {
                    "diaspore/stdlib/__init__.py", "diaspore/stdlib/_pyparse.py",
                }
        assert not any(name.startswith("diaspore/cpython/") and name.endswith(".py")
                       for name in names)
        archive.extractall(installed)
    program = r"""
import io
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from diaspore.compiler import compile_bundle
from diaspore.stdlib_store import load_stdlib_store
from diaspore.vm import Interp
store = load_stdlib_store()
assert len(store["modules"]) > 400 and not store["modules"]._cache
source = Path("main.py")
source.write_text('import json\nprint(json.dumps({"a": [1, 2.5, "x"]}, sort_keys=True))\n')
for standalone in (False, True):
    bundle = compile_bundle(str(source), self_contained=standalone)
    output, errors = io.StringIO(), io.StringIO()
    assert Interp(bundle, stdout=output, stderr=errors).run() == 0, errors.getvalue()
    assert output.getvalue() == '{"a": [1, 2.5, "x"]}\n'
    assert ("stdlib" not in bundle) == standalone
"""
    result = subprocess.run([sys.executable, "-I", "-c", program, str(installed)],
                            cwd=tmp_path, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
