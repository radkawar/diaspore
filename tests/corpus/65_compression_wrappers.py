import gzip
import zlib


# Fixtures emitted by CPython zlib, including actual fixed and dynamic trees.
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
gz_dynamic = (
    b'\x1f\x8b\x08\x00\x00\x00\x00\x00\x02\x03\xed\xca\xdb\x11\x820'
    b'\x14@\xc1Vn\x05\xf4\xa4\x10\x04Q\xa2!\xc1G\xf5'
    b':V`\x01\xfby\xe6l\x9dR\xdc\xdb\xdc/q,'
    b'\xf9\xb1\xc6\x98\x9fqn\xd7\xdb\x16yO%\xeaw_'
    b'\x0e\xefW\x0c\xf9\xd4\xfd\n\x86a\x18\x86a\x18\x86a'
    b'\x18\x86a\x18\x86a\x18\x86a\x18\x86a\x18\x86a\x18'
    b'\x86a\x18\x86a\x18\x86a\x18\x86a\x18\x86a\xf8\x0f'
    b'\xfc\x019\xba\xab\xf9\xbc4\x00\x00'
)
stored = b'x\x01\x01\x0c\x00\xf3\xffstored block\x1f\x80\x04\xbd'
fixed_data = b'hello DEFLATE! ' * 8
dynamic_data = b'the quick brown fox jumps over the lazy dog. ' * 300

print('fixture block types', (stored[2] >> 1) & 3, (fixed[2] >> 1) & 3, (dynamic[2] >> 1) & 3)
for packed, plain in [(stored, b'stored block'), (fixed, fixed_data), (dynamic, dynamic_data)]:
    print('inflate', zlib.decompress(packed) == plain)
    print('raw', zlib.decompress(packed[2:-4], -15) == plain)
    print('auto', zlib.decompress(packed, 0) == plain, zlib.decompress(packed, 47) == plain)
    print('trailing', zlib.decompress(packed + b'ignored') == plain)
print('gzip fixture', gzip.decompress(gz_dynamic) == dynamic_data)
print('gzip via zlib', zlib.decompress(gz_dynamic, 31) == dynamic_data, zlib.decompress(gz_dynamic, 47) == dynamic_data)
print('automatic window size', zlib.decompress(dynamic, 32) == dynamic_data)
print('gzip automatic window', zlib.decompress(gz_dynamic, 16) == dynamic_data, zlib.decompress(gz_dynamic, 32) == dynamic_data)

for plain in [b'', b'a', b'abc' * 40, bytes(list(range(256)))]:
    for level in [-1, 0, 1, 6, 9]:
        print('round trip', len(plain), level, zlib.decompress(zlib.compress(plain, level)) == plain)
        print('gzip round trip', gzip.decompress(gzip.compress(plain, level, mtime=0)) == plain)
    for window in [-15, -9, 9, 15, 25, 31]:
        print('window', window, zlib.decompress(zlib.compress(plain, wbits=window), window) == plain)

# Stored compression must split blocks at the 16-bit block-length boundary.
large = b'a' * 65536
print('multiple blocks', zlib.decompress(zlib.compress(large, 0)) == large)
print('large gzip', gzip.decompress(gzip.compress(large, 0, mtime=0)) == large)
print('native timestamp', gzip.decompress(gzip.compress(b'now')) == b'now')
print('explicit timestamp', gzip.compress(b'time', mtime=123456)[4:8] == bytes([64, 226, 1, 0]))

other = gzip.compress(b'last member', mtime=0)
print('concatenated', gzip.decompress(gz_dynamic + other) == dynamic_data + b'last member')
print('padded members', gzip.decompress(gz_dynamic + b'\x00\x00' + other + b'\x00') == dynamic_data + b'last member')
print('zlib first member', zlib.decompress(gz_dynamic + other, 31) == dynamic_data)
print('empty gzip input', gzip.decompress(b'') == b'')

# Optional extra, filename, comment, text flag, and header CRC fields.
header = bytes([31, 139, 8, 31, 0, 0, 0, 0, 0, 255, 3, 0]) + b'xyzname.txt\x00comment\x00'
header_crc = zlib.crc32(header) & 65535
with_header = header + bytes([header_crc & 255, header_crc >> 8]) + gz_dynamic[10:]
print('optional header', gzip.decompress(with_header) == dynamic_data)
print('optional header zlib', zlib.decompress(with_header, 31) == dynamic_data)

for plain in [b'', b'123456789', bytes(list(range(256))), b'a' * 6000]:
    print('checksums', zlib.crc32(plain), zlib.adler32(plain))
    middle = len(plain) // 2
    print('incremental', zlib.crc32(plain[middle:], zlib.crc32(plain[:middle])) == zlib.crc32(plain), zlib.adler32(plain[middle:], zlib.adler32(plain[:middle])) == zlib.adler32(plain))
for seed in [-1, 0, 1, 4294967295, 4294967296]:
    print('seeded checksums', seed, zlib.crc32(b'', seed), zlib.adler32(b'', seed), zlib.crc32(b'data', seed), zlib.adler32(b'data', seed))
