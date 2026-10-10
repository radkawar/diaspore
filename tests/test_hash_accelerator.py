"""Keep private hash acceleration equivalent to portable incremental hashing."""

import hashlib
import importlib.util
from pathlib import Path

import pytest

from .conftest import run_cpython, run_diaspore_py

ALGORITHMS = ("md5", "sha1", "sha224", "sha256", "sha384", "sha512")


@pytest.fixture(params=[False, True], ids=["portable", "native-engine"])
def provider(request):
    path = Path(__file__).resolve().parents[1] / "src/diaspore/stdlib/_hashcore.py"
    spec = importlib.util.spec_from_file_location("portable_hashcore", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module._native_hash is None
    if request.param:
        # Exercise the private engine contract with actual incremental hashing.
        module._native_hash = lambda name: hashlib.new(name, usedforsecurity=False)
    return module


@pytest.mark.parametrize("name", ALGORITHMS)
def test_incremental_hash_metadata_copy_and_state(provider, name):
    expected = hashlib.new(name, usedforsecurity=False)
    actual = provider._Hash(name)
    assert (actual.name, actual.block_size, actual.digest_size) == (
        expected.name,
        expected.block_size,
        expected.digest_size,
    )
    for size in (0, 1, 63, 64, 65, 127, 128, 129, 257):
        chunk = bytes(index % 251 for index in range(size))
        assert actual.update(chunk) is None
        expected.update(chunk)
        assert actual.digest() == expected.digest()
        assert actual.hexdigest() == expected.hexdigest()
        assert actual.digest() == expected.digest()
    copied = actual.copy()
    expected_copy = expected.copy()
    copied.update(b"copy")
    expected_copy.update(b"copy")
    actual.update(b"original")
    expected.update(b"original")
    assert copied.digest() == expected_copy.digest()
    assert actual.digest() == expected.digest()
    assert copied.digest() != actual.digest()
    for attribute in ("name", "block_size", "digest_size"):
        with pytest.raises(AttributeError):
            setattr(actual, attribute, "changed")


@pytest.mark.parametrize("name", ALGORITHMS)
def test_hash_buffer_validation_precedes_engine_mutation(provider, name):
    value = provider._Hash(name, bytearray(b"initial"))
    expected = hashlib.new(name, b"initial", usedforsecurity=False)
    data = bytearray(b"buffer")
    value.update(memoryview(data))
    expected.update(data)
    data[:] = b"change"
    assert value.digest() == expected.digest()
    invalid = ["text", 3, [1, 2], memoryview(b"abcdef")[::2]]
    released = memoryview(b"released")
    released.release()
    invalid.append(released)
    for data in invalid:
        with pytest.raises((TypeError, BufferError, ValueError)) as reference_error:
            expected.update(data)
        with pytest.raises(type(reference_error.value)):
            value.update(data)
        assert value.digest() == expected.digest()
    with pytest.raises(TypeError):
        value.update(data=b"keyword")
    with pytest.raises(TypeError):
        value.digest(1)
    with pytest.raises(TypeError):
        value.hexdigest(1)
    with pytest.raises(TypeError):
        value.copy(1)


def test_hash_rejects_unimplemented_names(provider):
    with pytest.raises(ValueError, match="unsupported hash type unknown"):
        provider._Hash("unknown")


def test_private_native_hash_path_matches_cpython(tmp_path):
    source = tmp_path / "hash_state.py"
    source.write_text("""
import hashlib
for name in ("md5", "sha1", "sha224", "sha256", "sha384", "sha512"):
    value = hashlib.new(name, b"initial")
    value.update(bytearray(b"bytes"))
    value.update(memoryview(b"view"))
    print(value.name, value.block_size, value.digest_size, value.hexdigest())
    copied = value.copy()
    copied.update(b"copy")
    value.update(b"original")
    print(value.digest().hex(), copied.hexdigest(), copied.digest() == copied.digest())
    before = value.digest()
    for invalid in ("text", [1, 2], memoryview(b"abcdef")[::2]):
        try:
            value.update(invalid)
        except Exception as error:
            print(type(error).__name__, value.digest() == before)
""")
    expected = run_cpython(source)
    assert expected.code == 0, expected.err_line
    actual = run_diaspore_py(source)
    assert actual == expected
