import gzip
import io
import zlib


fixed = b'x\x01\xcbH\xcd\xc9\xc9Wpqu\xf3q\x0cqUT\xc8\xa0\x17\x17\x00\x80p#Q'
dynamic = (
    b'x\x9c\xed\xca\xdb\x11\x820\x14@\xc1Vn\x05\xf4\xa4'
    b'\x10\x04Q\xa2!\xc1G\xf5:V`\x01\xfby\xe6l'
    b'\x9dR\xdc\xdb\xdc/q,\xf9\xb1\xc6\x98\x9fqn\xd7'
    b'\xdb\x16yO%\xeaw_\x0e\xefW\x0c\xf9\xd4\xfd\n'
    b'\x86a\x18\x86a\x18\x86a\x18\x86a\x18\x86a\x18\x86'
    b'a\x18\x86a\x18\x86a\x18\x86a\x18\x86a\x18\x86a'
    b'\x18\x86a\x18\x86a\xf8\x0f\xfc\x01\x84Z\x14R'
)
for fixture, expected in ((fixed, b'hello DEFLATE! ' * 8),
                          (dynamic, b'the quick brown fox jumps over the lazy dog. ' * 300)):
    for step in (1, 3, 11):
        decoder = zlib.decompressobj()
        parts = []
        for pos in range(0, len(fixture), step):
            parts.append(decoder.decompress(fixture[pos:pos + step]))
        parts.append(decoder.flush())
        print("split fixture", step, b"".join(parts) == expected, decoder.eof, decoder.unused_data)
    decoder = zlib.decompressobj()
    parts = [decoder.decompress(fixture + b"trailer", 7)]
    while not decoder.eof:
        parts.append(decoder.decompress(decoder.unconsumed_tail, 7))
    print("limited output", b"".join(parts) == expected, decoder.unused_data)

plain = b"streamed payload" * 30
for window in (-15, 15, 31):
    compressor = zlib.compressobj(wbits=window)
    first = compressor.compress(plain[:100]) + compressor.flush(zlib.Z_SYNC_FLUSH)
    second = compressor.compress(memoryview(plain)[100:]) + compressor.flush()
    decoder = zlib.decompressobj(window)
    prefix = decoder.decompress(first)
    suffix = decoder.decompress(second + b"suffix")
    print("stream round trip", window, prefix == plain[:100], prefix + suffix == plain,
          decoder.eof, decoder.unused_data)
    print("post eof", decoder.decompress(b"more"), decoder.unused_data)
    print("one shot", zlib.decompress(first + second, window) == plain)
    try:
        compressor.compress(b"late")
    except zlib.error:
        print("finished compressor")

for mode in (zlib.Z_PARTIAL_FLUSH, zlib.Z_FULL_FLUSH, zlib.Z_BLOCK):
    compressor = zlib.compressobj()
    encoded = compressor.compress(b"abc") + compressor.flush(mode)
    encoded += compressor.compress(b"def") + compressor.flush()
    print("flush mode", mode, zlib.decompress(encoded))

truncated = zlib.decompressobj()
print("truncated object", truncated.decompress(fixed[:-1]) == b'hello DEFLATE! ' * 8, truncated.eof)
try:
    zlib.decompress(fixed[:-1])
except zlib.error:
    print("truncated one shot")
try:
    zlib.decompress(fixed[:-1] + b"\x00")
except zlib.error:
    print("checksum error")

archive = gzip.compress(b"first", mtime=0) + b"\x00\x00" + gzip.compress(b"second", mtime=123)
print("gzip members", gzip.decompress(archive))
stream = io.BytesIO()
with gzip.GzipFile(fileobj=stream, mode="wb", mtime=123) as writer:
    writer.write(b"hello ")
    writer.flush()
    writer.write(b"world\nsecond line\n")
encoded = stream.getvalue()
print("gzip written", gzip.decompress(encoded))
with gzip.GzipFile(fileobj=io.BytesIO(encoded), mode="rb") as reader:
    print("gzip read", reader.read(3), reader.readline(), reader.read(), reader.mtime)
