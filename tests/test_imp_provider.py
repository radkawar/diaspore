"""Exercise sandbox import primitives and CPython source-hash compatibility."""

import _imp
import importlib.util
from pathlib import Path

import pytest

PROVIDER_PATH = Path(__file__).resolve().parents[1] / "src/diaspore/stdlib/_imp.py"


@pytest.fixture
def provider():
    spec = importlib.util.spec_from_file_location("diaspore_imp_test", PROVIDER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("key", [0, 1, -1, 168627623, (1 << 63) - 1, -(1 << 63)])
@pytest.mark.parametrize("size", [0, 1, 7, 8, 9, 15, 16, 31, 255, 256, 257])
def test_source_hash_matches_cpython(provider, key, size):
    source = bytes(index % 256 for index in range(size))
    assert provider.source_hash(key, source) == _imp.source_hash(key, source)
    assert provider.source_hash(key, bytearray(source)) == _imp.source_hash(key, source)


def test_import_lock_is_recursive_and_owned(provider):
    import threading

    assert not provider.lock_held()
    with pytest.raises(RuntimeError, match="not holding the import lock"):
        provider.release_lock()
    provider.acquire_lock()
    provider.acquire_lock()
    assert provider.lock_held()
    errors = []

    def release_from_other_thread():
        try:
            provider.release_lock()
        except RuntimeError as error:
            errors.append(str(error))

    thread = threading.Thread(target=release_from_other_thread)
    thread.start()
    thread.join()
    assert errors == ["not holding the import lock"]
    provider.release_lock()
    assert provider.lock_held()
    provider.release_lock()
    assert not provider.lock_held()


def test_native_creation_and_provider_boundaries(provider):
    import sys
    from types import SimpleNamespace

    assert provider.is_builtin("sys") == -1
    assert provider.create_builtin(SimpleNamespace(name="sys")) is sys
    assert provider.exec_builtin(sys) == 0
    assert provider.is_builtin("not_a_native_module") == 0
    assert provider.create_builtin(SimpleNamespace(name="not_a_native_module")) is None
    assert provider.extension_suffixes() == []
    assert provider.find_frozen("importlib") is None
    assert provider.is_frozen("importlib") is False
    with pytest.raises(ImportError):
        provider.is_frozen_package("importlib")
    with pytest.raises(ImportError):
        provider.get_frozen_object("importlib")
    with pytest.raises(ImportError):
        provider.create_dynamic(SimpleNamespace(name="untrusted_extension"))


def test_import_provider_runs_in_sandbox(tmp_path):
    from .conftest import run_diaspore_py

    source = tmp_path / "main.py"
    source.write_text(
        "import _imp\n"
        "import sys\n"
        "assert not _imp.lock_held()\n"
        "_imp.acquire_lock()\n"
        "_imp.acquire_lock()\n"
        "_imp.release_lock()\n"
        "assert _imp.lock_held()\n"
        "_imp.release_lock()\n"
        "assert not _imp.lock_held()\n"
        "class Spec:\n"
        "    name = 'sys'\n"
        "assert _imp.create_builtin(Spec()) is sys\n"
        "assert _imp.exec_builtin(sys) == 0\n"
        "assert _imp.extension_suffixes() == []\n"
        f"assert _imp.source_hash(123, b'abc') == {_imp.source_hash(123, b'abc')!r}\n"
    )
    result = run_diaspore_py(source)
    assert result.code == 0, result.err_line
    assert result.stdout == ""
