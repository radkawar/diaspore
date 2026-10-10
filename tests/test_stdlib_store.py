"""Check compiled-store identity, cache inputs, and sandbox bridge isolation."""

import gzip
import hashlib
import json
from types import SimpleNamespace

import pytest

from diaspore import compiler, stdlib_store


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


def test_store_cache_keys_source_and_compiler_bytes(tmp_path, monkeypatch):
    source = tmp_path / "stdlib"
    source.mkdir()
    module = source / "sample.py"
    module.write_text("VALUE = 1\n", encoding="utf-8")
    implementation = tmp_path / "compiler.py"
    implementation.write_text("compiler input one\n", encoding="utf-8")
    monkeypatch.setattr(compiler, "STDLIB_DIRS", (str(source),))
    monkeypatch.setattr(compiler, "__file__", str(implementation))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setattr(stdlib_store, "_store", None)
    original_compile = stdlib_store._compile
    first = stdlib_store.load_stdlib_store()
    monkeypatch.setattr(stdlib_store, "_store", None)

    def fail_compile(*args):
        raise AssertionError("unchanged installed sources must use the persistent cache")

    monkeypatch.setattr(stdlib_store, "_compile", fail_compile)
    assert stdlib_store.load_stdlib_store() == first
    monkeypatch.setattr(stdlib_store, "_compile", original_compile)
    monkeypatch.setattr(stdlib_store, "_store", None)
    module.write_text("VALUE = 2\n", encoding="utf-8")
    second = stdlib_store.load_stdlib_store()
    assert second["id"] != first["id"]
    monkeypatch.setattr(stdlib_store, "_store", None)
    implementation.write_text("compiler input two\n", encoding="utf-8")
    assert stdlib_store.load_stdlib_store()["id"] == second["id"]
    cache_files = list((tmp_path / "cache" / "diaspore" / "stdlib").glob("*.json.gz"))
    assert len(cache_files) == 3


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


def test_corrupt_cached_store_is_not_published(tmp_path, monkeypatch):
    source = tmp_path / "stdlib"
    source.mkdir()
    (source / "sample.py").write_text("VALUE = 1\n", encoding="utf-8")
    monkeypatch.setattr(compiler, "STDLIB_DIRS", (str(source),))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setattr(stdlib_store, "_store", None)
    original = stdlib_store.load_stdlib_store()
    path = next((tmp_path / "cache" / "diaspore" / "stdlib").glob("*.json.gz"))
    corrupted = json.loads(json.dumps(original))
    corrupted["modules"]["sample"]["body"] = []
    path.write_bytes(gzip.compress(json.dumps(corrupted).encode()))
    monkeypatch.setattr(stdlib_store, "_store", None)
    with pytest.raises(ValueError, match="content id mismatch"):
        stdlib_store.load_stdlib_store()
    assert stdlib_store._store is None
