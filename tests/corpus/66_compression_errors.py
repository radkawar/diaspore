import gzip
import zlib


def broken_zlib(data, window=15):
    try:
        zlib.decompress(data, window)
        print('unexpected zlib success')
    except zlib.error:
        print('zlib error')


def broken_gzip(data):
    try:
        gzip.decompress(data)
        print('unexpected gzip success')
    except gzip.BadGzipFile:
        print('bad gzip')
    except EOFError:
        print('truncated gzip')
    except zlib.error:
        print('bad deflate')


for stream in [b'', b'x', b'xx', b'\x78\x20', b'\x78\x9c', b'\x78\x9c\x07']:
    broken_zlib(stream)
for stream in [b'', b'\x07', b'\x01\x01\x00\x00\x00x', b'\x01\x01\x00\xfe\xff', b'\xfd\x00\x00', b'\x05\x00\x00\x00']:
    broken_zlib(stream, -15)

packed = zlib.compress(b'checksum')
broken_zlib(packed[:-1])
broken_zlib(packed[:-1] + bytes([packed[-1] ^ 1]))
broken_zlib(packed, 8)
for window in [-7, 7, 16, 32, 48]:
    broken_zlib(packed, window)
for level in [-2, 10]:
    try:
        zlib.compress(b'x', level)
    except zlib.error:
        print('bad compression level')
try:
    zlib.decompress(packed, bufsize=-1)
except ValueError:
    print('bad buffer size')

packed = gzip.compress(b'checksum', mtime=0)
broken_gzip(b'not gzip')
broken_gzip(b'\x1f\x8b')
broken_gzip(packed[:2] + b'\x09' + packed[3:])
broken_gzip(packed[:10])
broken_gzip(packed[:-1])
broken_gzip(packed[:-8] + bytes([packed[-8] ^ 1]) + packed[-7:])
broken_gzip(packed[:-4] + bytes([packed[-4] ^ 1]) + packed[-3:])
broken_gzip(packed + b'garbage')
broken_gzip(packed + packed[:-1])
broken_gzip(packed[:10] + b'\x07' + packed[-8:])
broken_zlib(packed[:-1], 31)
broken_zlib(packed[:-8] + bytes([packed[-8] ^ 1]) + packed[-7:], 31)

# Exercise truncated extra, filename, comment, and header-CRC fields.
for flags, suffix in [(4, b'\x02\x00x'), (8, b'name'), (16, b'comment'), (2, b'\x00')]:
    header = bytes([31, 139, 8, flags, 0, 0, 0, 0, 0, 255])
    broken_gzip(header + suffix)

header = bytes([31, 139, 8, 2, 0, 0, 0, 0, 0, 255])
checksum = (zlib.crc32(header) ^ 1) & 65535
broken_zlib(header + bytes([checksum & 255, checksum >> 8]) + packed[10:], 31)
