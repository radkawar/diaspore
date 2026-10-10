"""Check prebuilt-store identity, lazy loading, and sandbox bridge isolation."""

import hashlib
import json
import struct
import zlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from diaspore import compiler, dspb, stdlib_store, stdlib_store_codec
from diaspore.vm import bundle_digest


def test_store_identity_excludes_distribution_metadata(tmp_path, monkeypatch):
    own = tmp_path / "stdlib"
    vendor = tmp_path / "cpython"
    own.mkdir()
    vendor.mkdir()
    (own / "sample.py").write_text("VALUE = '\\ud800'\n", encoding="utf-8")
    (vendor / "sample.py").write_text("VALUE = 2\n", encoding="utf-8")
    (vendor / "LICENSE").write_text("PSF notice\n", encoding="utf-8")
    (vendor / "UNICODE_LICENSE").write_text("Unicode notice\n", encoding="utf-8")
    (vendor / "stdlib_manifest.json").write_text('{"modules": []}\n', encoding="utf-8")
    monkeypatch.setattr(compiler, "STDLIB_DIRS", (str(own), str(vendor)))
    first = stdlib_store.compile_stdlib_store()
    assert first["licenses"] == {"cpython": "PSF notice\n", "unicode": "Unicode notice\n"}
    assert first["manifest"] == {"modules": []}
    payload = {"diaspore_stdlib": 1, "modules": first["modules"]}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    expected = "sha256:" + hashlib.sha256(canonical.encode("utf-8", "backslashreplace")).hexdigest()
    assert first["id"] == expected
    (vendor / "LICENSE").write_text("Revised notice\n", encoding="utf-8")
    second = stdlib_store.compile_stdlib_store()
    assert second["id"] == first["id"]
    assert second["licenses"]["cpython"] == "Revised notice\n"
    (own / "sample.py").write_text("VALUE = 3\n", encoding="utf-8")
    assert stdlib_store.compile_stdlib_store()["id"] != first["id"]


def test_installed_store_never_compiles_sources(tmp_path, monkeypatch):
    source = tmp_path / "stdlib"
    source.mkdir()
    module = source / "sample.py"
    module.write_text("VALUE = 1\n", encoding="utf-8")
    monkeypatch.setattr(compiler, "STDLIB_DIRS", (str(source),))
    compiled = stdlib_store.compile_stdlib_store()
    path = tmp_path / "stdlib.dsps"
    path.write_bytes(stdlib_store_codec.encode_stdlib_store(compiled))
    monkeypatch.setattr(stdlib_store, "_store_path", lambda: str(path))
    monkeypatch.setattr(stdlib_store, "_store", None)

    def fail_compile(*args):
        raise AssertionError("installed loading must not read or compile source trees")

    monkeypatch.setattr(stdlib_store, "_sources", fail_compile)
    monkeypatch.setattr(stdlib_store, "_compile", fail_compile)
    module.write_text("VALUE = 2\n", encoding="utf-8")
    first = stdlib_store.load_stdlib_store()
    assert first["id"] == compiled["id"]
    assert stdlib_store.load_stdlib_store() is first
    assert stdlib_store.stdlib_module_names() == ["sample"]
    assert "sample" in first["modules"]
    assert first["modules"].module_file("sample") == "<stdlib>/sample.py"
    assert first["modules"]._cache == {}
    loaded = stdlib_store.get_stdlib_module("sample")
    assert loaded == compiled["modules"]["sample"]
    assert set(first["modules"]._cache) == {"sample"}
    loaded["body"].clear()
    assert stdlib_store.get_stdlib_module("sample") == compiled["modules"]["sample"]


@pytest.mark.parametrize("relative", [
    "src/diaspore/stdlib.dsps",
    "runtimes/go/interp/stdlib.dsps",
    "runtimes/ts/stdlib.dsps",
    "runtimes/lua/stdlib.dsps",
])
def test_generated_stores_share_identity_and_lazy_index(relative):
    path = Path(__file__).resolve().parent.parent / relative
    with path.open("rb") as artifact:
        assert artifact.read(6) == b"DSPS02"
        index_length = struct.unpack("<I", artifact.read(4))[0]
        index_digest = artifact.read(32)
        index = artifact.read(index_length)
    assert hashlib.sha256(index).digest() == index_digest
    dictionary_length = struct.unpack_from("<H", index, 32)[0]
    if relative.startswith("runtimes/lua/"):
        assert dictionary_length == 0
    else:
        assert 0 < dictionary_length <= 32768
    record = 32 + 2 + dictionary_length
    assert struct.unpack_from("<I", index, record)[0] > 0
    record += 4
    for _ in range(2):  # First record's module name and filename.
        length = struct.unpack_from("<H", index, record)[0]
        record += 2 + length
    assert index[record + 12] == (0 if relative.startswith("runtimes/lua/") else 1)
    store = stdlib_store_codec.load_stdlib_store(path)
    assert store["id"] == stdlib_store.get_stdlib_id()
    modules = store["modules"]
    assert list(modules) == stdlib_store.stdlib_module_names()
    assert "json" in modules
    assert modules.module_file("json") == "<stdlib>/json/__init__.py"
    assert modules._cache == {}
    assert "licenses" not in store
    assert "manifest" not in store
    assert modules["json"] == stdlib_store.get_stdlib_module("json")
    assert set(modules._cache) == {"json"}
    assert not path.with_suffix(".json").exists()
    assert not path.with_suffix(".json.gz").exists()


def test_sandbox_bridge_does_not_read_installed_sources(monkeypatch):
    module = {"name": "sample", "file": "<stdlib>/sample.py", "body": []}
    bridge = SimpleNamespace(
        get_id=lambda: "sha256:" + "a" * 64,
        module_names=lambda: ["sample"],
        get_module=lambda name: module if name == "sample" else None,
    )
    monkeypatch.setattr(stdlib_store, "_bridge", bridge)

    def fail_load():
        raise AssertionError("sandbox bridge must not open the host source/cache tree")

    monkeypatch.setattr(stdlib_store, "load_stdlib_store", fail_load)
    assert stdlib_store.get_stdlib_id() == "sha256:" + "a" * 64
    assert stdlib_store.stdlib_module_names() == ["sample"]
    assert stdlib_store.get_stdlib_module("sample") is module
    assert stdlib_store.get_stdlib_module("absent") is None


def test_corrupt_installed_index_is_not_published(tmp_path, monkeypatch):
    source = tmp_path / "stdlib"
    source.mkdir()
    (source / "sample.py").write_text("VALUE = 1\n", encoding="utf-8")
    monkeypatch.setattr(compiler, "STDLIB_DIRS", (str(source),))
    original = stdlib_store.compile_stdlib_store()
    data = bytearray(stdlib_store_codec.encode_stdlib_store(original))
    data[42] ^= 1
    path = tmp_path / "stdlib.dsps"
    path.write_bytes(data)
    monkeypatch.setattr(stdlib_store, "_store_path", lambda: str(path))
    monkeypatch.setattr(stdlib_store, "_store", None)
    with pytest.raises(ValueError, match="index"):
        stdlib_store.load_stdlib_store()
    assert stdlib_store._store is None


def test_missing_binary_store_does_not_fall_back_to_json_or_sources(tmp_path, monkeypatch):
    path = tmp_path / "stdlib.dsps"
    path.with_suffix(".json").write_text("{}", encoding="utf-8")
    path.with_suffix(".json.gz").write_bytes(b"\x1f\x8b")
    monkeypatch.setattr(stdlib_store, "_store_path", lambda: str(path))
    monkeypatch.setattr(stdlib_store, "_store", None)

    def fail_compile(*args):
        raise AssertionError("missing binary store must not compile installed sources")

    monkeypatch.setattr(stdlib_store, "_sources", fail_compile)
    monkeypatch.setattr(stdlib_store, "_compile", fail_compile)
    with pytest.raises(FileNotFoundError):
        stdlib_store.load_stdlib_store()
    assert stdlib_store._store is None


def _dictionary_store(dictionary, *, compress=True, damage=None):
    module = {"name": "sample", "file": "<stdlib>/sample.py", "body": []}
    store = {"diaspore_stdlib": 1, "modules": {"sample": module}}
    store["id"] = "sha256:" + bundle_digest(store)
    decoded = dspb.encode({"diaspore": 1, "main": "sample", "modules": store["modules"]})
    payload = decoded
    if compress:
        options = {"zdict": dictionary} if dictionary else {}
        encoder = zlib.compressobj(level=9, wbits=-15, **options)
        payload = encoder.compress(decoded) + encoder.flush()
    if damage == "trailing":
        payload += b"extra"
    elif damage == "truncated":
        payload = payload[:-1]
    elif damage == "invalid":
        payload = b"\x07"  # Reserved DEFLATE block type.
    index = bytearray(bytes.fromhex(store["id"][7:]))
    index.extend(struct.pack("<H", len(dictionary)))
    index.extend(dictionary)
    index.extend(struct.pack("<I", 1))
    for text in ("sample", module["file"]):
        encoded = text.encode("utf-8")
        index.extend(struct.pack("<H", len(encoded)))
        index.extend(encoded)
    index.extend(struct.pack("<IIIB32s", 0, len(payload), len(decoded), int(compress),
                             hashlib.sha256(payload).digest()))
    data = (b"DSPS02" + struct.pack("<I", len(index))
            + hashlib.sha256(index).digest() + index + payload)
    return data, store


@pytest.mark.parametrize("dictionary", [b"", b"sample<stdlib>/sample.py" * 16])
@pytest.mark.parametrize("compress", [False, True], ids=["raw", "deflate"])
def test_dictionary_container_roundtrip_is_lazy(dictionary, compress):
    data, expected = _dictionary_store(dictionary, compress=compress)
    store = stdlib_store_codec.decode_stdlib_store(data)
    assert store["id"] == expected["id"]
    modules = store["modules"]
    assert list(modules) == ["sample"]
    assert modules.module_file("sample") == "<stdlib>/sample.py"
    assert modules._cache == {}
    assert modules["sample"] == expected["modules"]["sample"]
    assert set(modules._cache) == {"sample"}


@pytest.mark.parametrize("damage", ["trailing", "truncated", "invalid"])
@pytest.mark.parametrize("dictionary", [b"", b"sample<stdlib>/sample.py" * 16])
def test_corrupt_deflate_payload_is_rejected_lazily(dictionary, damage):
    data, _ = _dictionary_store(dictionary, damage=damage)
    modules = stdlib_store_codec.decode_stdlib_store(data)["modules"]
    assert modules._cache == {}
    with pytest.raises(ValueError):
        modules["sample"]
    assert modules._cache == {}


def test_dictionary_bytes_are_covered_by_index_digest():
    data, _ = _dictionary_store(b"sample<stdlib>/sample.py" * 16)
    damaged = bytearray(data)
    damaged[42 + 32 + 2] ^= 1
    with pytest.raises(ValueError, match="index"):
        stdlib_store_codec.decode_stdlib_store(damaged)


@pytest.mark.parametrize("dictionary_length", [32769, 65535])
def test_oversized_dictionary_is_rejected(dictionary_length):
    data, _ = _dictionary_store(b"x" * dictionary_length)
    with pytest.raises(ValueError):
        stdlib_store_codec.decode_stdlib_store(data)


def test_truncated_dictionary_is_rejected():
    index = bytes(32) + struct.pack("<H", 100) + b"short"
    data = (b"DSPS02" + struct.pack("<I", len(index))
            + hashlib.sha256(index).digest() + index)
    with pytest.raises(ValueError):
        stdlib_store_codec.decode_stdlib_store(data)


def test_legacy_store_version_is_rejected():
    data, _ = _dictionary_store(b"")
    with pytest.raises(ValueError):
        stdlib_store_codec.decode_stdlib_store(b"DSPS01" + data[6:])
