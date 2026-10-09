"""Provide one-shot gzip compression and concatenated-member decompression.

Input and output are bytes. DEFLATE blocks and optional gzip header fields are
supported, including header CRC, data CRC, and uncompressed-size validation.
Compression emits stored blocks at all valid levels, with an explicit mtime or
the current native time. File and streaming interfaces are unsupported.
"""

import time
import zlib


class BadGzipFile(OSError):
    """Report invalid gzip headers, checksums, and size trailers."""


def compress(data, compresslevel=9, mtime=None):
    """Return gzip bytes, using native time when mtime is None."""
    zlib._require_bytes(data)
    zlib._level(compresslevel)
    if mtime is None:
        mtime = int(time.time())
    elif isinstance(mtime, (int, float)):
        mtime = int(mtime)
    else:
        raise TypeError("mtime must be a number")
    if mtime < 0 or mtime > 4294967295:
        raise OverflowError("mtime out of range")
    return zlib._gzip_compress(data, compresslevel, mtime)


def _need(data, end):
    if end > len(data):
        raise EOFError("Compressed file ended before the end-of-stream marker was reached")


def _member(data, pos):
    start = pos
    if data[pos:pos + 2] != b"\x1f\x8b":
        raise BadGzipFile("Not a gzipped file")
    _need(data, pos + 10)
    if data[pos + 2] != 8:
        raise BadGzipFile("Unknown compression method")
    flags = data[pos + 3]
    if flags & 224:
        raise BadGzipFile("Reserved gzip header flags are set")
    pos += 10
    if flags & 4:
        _need(data, pos + 2)
        size = data[pos] | (data[pos + 1] << 8)
        pos += 2 + size
        _need(data, pos)
    for flag in (8, 16):
        if flags & flag:
            while True:
                _need(data, pos + 1)
                byte = data[pos]
                pos += 1
                if byte == 0:
                    break
    if flags & 2:
        _need(data, pos + 2)
        expected = data[pos] | (data[pos + 1] << 8)
        if zlib._crc(data[start:pos]) & 65535 != expected:
            raise BadGzipFile("Corrupt gzip header CRC")
        pos += 2
    try:
        out, pos = zlib._inflate(data, pos)
    except zlib.error as exc:
        if str(exc) == "incomplete or truncated stream":
            raise EOFError(
                "Compressed file ended before the end-of-stream marker was reached"
            ) from None
        raise
    _need(data, pos + 8)
    if zlib._get32(data, pos) != zlib._crc(out):
        raise BadGzipFile("CRC check failed")
    if zlib._get32(data, pos + 4) != len(out) & 4294967295:
        raise BadGzipFile("Incorrect length of data produced")
    return out, pos + 8


def decompress(data):
    """Decode all gzip members, accepting zero padding after each member."""
    zlib._require_bytes(data)
    out = []
    pos = 0
    while pos < len(data):
        member, pos = _member(data, pos)
        out.extend(member)
        while pos < len(data) and data[pos] == 0:
            pos += 1
    return bytes(out)


def open(*args, **kwargs):
    """Reject unsupported gzip file interfaces."""
    raise NotImplementedError("gzip file interfaces are not supported")


class GzipFile:
    """Reject unsupported gzip stream interfaces."""

    def __init__(self, *args, **kwargs):
        raise NotImplementedError("gzip stream interfaces are not supported")
