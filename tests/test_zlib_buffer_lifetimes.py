"""Release internal compression views without invalidating caller-owned leases."""

import importlib.util
import json
import subprocess
import zlib
from pathlib import Path

import pytest


def load_provider():
    path = Path(__file__).resolve().parents[1] / "src/diaspore/stdlib/zlib.py"
    spec = importlib.util.spec_from_file_location("portable_zlib", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("operation", ["crc32", "adler32", "compress", "stream"])
def test_compression_releases_internal_views(operation):
    provider = load_provider()
    original = b"payload" * 10000
    data = bytearray(original)
    if operation == "stream":
        compressor = provider.compressobj()
        result = compressor.compress(data) + compressor.flush()
    else:
        result = getattr(provider, operation)(data)
    data.extend(b"resized")
    if operation in ("compress", "stream"):
        assert zlib.decompress(result) == original
    else:
        assert result == getattr(zlib, operation)(original)


@pytest.mark.parametrize("buffered", [False, True])
def test_decompression_releases_input_but_retains_pending_bytes(buffered):
    provider = load_provider()
    original = b"streamed input" * 100
    compressed = bytearray(zlib.compress(original))
    decoder = provider._ZlibDecompressor() if buffered else provider.decompressobj()
    result = decoder.decompress(compressed, 1)
    compressed.clear()
    if buffered:
        result += decoder.decompress(b"")
    else:
        result += decoder.decompress(decoder.unconsumed_tail)
    assert result == original


def test_error_paths_release_views_even_with_retained_tracebacks():
    provider = load_provider()
    finished = provider.compressobj()
    finished.flush()
    operations = [
        lambda data: provider.crc32(data, object()),
        lambda data: provider.adler32(data, object()),
        lambda data: provider.compress(data, level=10),
        lambda data: provider.compress(data, wbits=0),
        finished.compress,
        lambda data: provider.decompressobj().decompress(data, -1),
        lambda data: provider._ZlibDecompressor().decompress(data, object()),
    ]
    failures = []
    for operation in operations:
        data = bytearray(b"input")
        try:
            operation(data)
        except (TypeError, ValueError, provider.error) as error:
            failures.append(error)
        else:
            raise AssertionError("invalid operation succeeded")
        data.extend(b"resized")
    assert len(failures) == len(operations)


def test_external_views_remain_live_and_noncontiguous_errors_release_internal_view():
    provider = load_provider()
    for function in (provider.crc32, provider.adler32, provider.compress):
        data = bytearray(b"input")
        external = memoryview(data)
        function(external)
        assert external.tobytes() == b"input"
        with pytest.raises(BufferError):
            data.extend(b"blocked")
        external.release()
        data.extend(b"allowed")

        data = bytearray(b"noncontiguous")
        external = memoryview(data)[::2]
        with pytest.raises(BufferError) as error:
            function(external)
        assert error.value is not None
        external.release()
        data.extend(b"allowed")


GZIP_BUFFER_SOURCE = """
import gzip
import io
import zlib

for operation in (zlib.crc32, zlib.adler32, zlib.compress):
    data = bytearray(b"buffer")
    operation(data)
    data.extend(b" resized")
    print(bytes(data))
    external = memoryview(data)
    operation(external)
    try:
        data.extend(b"blocked")
    except BufferError:
        print("external lease retained")
    external.release()
    data.extend(b" released")
    print(bytes(data))

raw = io.BytesIO()
with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as writer:
    writer.write(bytearray(b"first"))
    writer.flush()
    writer.write(bytearray(b"second"))
print(gzip.decompress(raw.getvalue()))
"""


def test_gzip_buffer_lifetimes_match_cpython(tmp_path):
    from .conftest import run_cpython, run_diaspore_py

    entry = tmp_path / "gzip_buffer_lifetimes.py"
    entry.write_text(GZIP_BUFFER_SOURCE)
    expected = run_cpython(entry)
    assert expected.code == 0, expected.err_line
    assert run_diaspore_py(entry) == expected


def test_ts_gzip_buffer_lifetimes(tmp_path, ts_vm):
    from .conftest import Result, assert_matches, build, run_cpython

    entry = tmp_path / "gzip_buffer_lifetimes.py"
    entry.write_text(GZIP_BUFFER_SOURCE)
    expected = run_cpython(entry)
    assert expected.code == 0, expected.err_line
    bundle = tmp_path / "gzip_buffer_lifetimes.dsp.json"
    bundle.write_text(json.dumps(build(entry)))
    process = subprocess.run(
        [*ts_vm, "run", str(bundle)], capture_output=True, text=True, timeout=120
    )
    assert process.returncode == 0, process.stderr
    lines = process.stderr.strip().splitlines()
    result = Result(process.stdout, process.returncode, lines[-1] if lines else "")
    assert_matches(result, expected, "TypeScript gzip buffer lifetimes")
