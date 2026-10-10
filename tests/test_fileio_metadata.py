"""Keep optional FileIO metadata handling aligned with CPython's C provider."""

import errno
import io
import os

import pytest

from .test_c_layer_semantics import load_provider, outcome


@pytest.mark.parametrize("number", [errno.EACCES, errno.ENOENT, errno.ENOSYS])
def test_unavailable_metadata_preserves_file_stream_operations(tmp_path, monkeypatch, number):
    provider = load_provider("_io")
    path = tmp_path / "content.bin"
    path.write_bytes(b"first\nsecond\n")

    def unavailable(fd):
        raise OSError(number, os.strerror(number))

    # Native FileIO uses C fstat, not os.fstat. Its successful stream behavior
    # remains the oracle while the portable provider cannot obtain metadata.
    monkeypatch.setattr(provider.os, "fstat", unavailable)
    with provider.FileIO(path, "r+") as portable, io.FileIO(path, "r+") as native:
        assert portable._blksize == provider.DEFAULT_BUFFER_SIZE
        assert portable.readall() == native.readall()
        assert portable.seek(0) == native.seek(0)
        assert portable.read(5) == native.read(5)
        assert portable.write(b"!") == native.write(b"!")
        assert portable.tell() == native.tell()


def test_bad_descriptor_remains_an_error():
    provider = load_provider("_io")
    assert outcome(provider.FileIO, 1 << 30) == outcome(io.FileIO, 1 << 30)


def test_metadata_bad_descriptor_closes_owned_file(tmp_path, monkeypatch):
    provider = load_provider("_io")
    path = tmp_path / "content.bin"
    path.write_bytes(b"value")
    descriptors = []
    real_fstat = os.fstat

    def bad_descriptor(fd):
        descriptors.append(fd)
        raise OSError(errno.EBADF, os.strerror(errno.EBADF))

    monkeypatch.setattr(provider.os, "fstat", bad_descriptor)
    assert outcome(provider.FileIO, path) == outcome(io.FileIO, 1 << 30)
    assert len(descriptors) == 1
    with pytest.raises(OSError) as error:
        real_fstat(descriptors[0])
    assert error.value.errno == errno.EBADF


def test_directory_and_open_errors_are_not_suppressed(tmp_path):
    provider = load_provider("_io")
    assert outcome(provider.FileIO, tmp_path) == outcome(io.FileIO, tmp_path)
    missing = tmp_path / "missing"
    assert outcome(provider.FileIO, missing) == outcome(io.FileIO, missing)

    def denied(path, flags):
        raise PermissionError(errno.EACCES, os.strerror(errno.EACCES), path)

    def open_denied(factory):
        return factory(missing, opener=denied)

    assert outcome(open_denied, provider.FileIO) == outcome(open_denied, io.FileIO)
