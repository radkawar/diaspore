import base64
import binascii
import math
import struct


class Index:
    def __index__(self):
        return 513


for order in ("<", ">", "!", "=", "@"):
    fmt = order + "xcbB?hHiIlLqQ4s5p"
    values = (b"Z", -128, 255, True, -1234, 65535, -123456, 4294967295,
              -42, 42, -9223372036854775808, 18446744073709551615,
              b"abcdef", b"abcdef")
    packed = struct.pack(fmt, *values)
    print(order, struct.calcsize(fmt), binascii.hexlify(packed), struct.unpack(fmt, packed))
print("native align", struct.calcsize("@blh0l"), struct.calcsize("=blh0l"))
print("native widths", struct.calcsize("@nNP"), struct.unpack("@nNP", struct.pack("@nNP", -1, 2, -1)))
print("zero", struct.pack("0s0p", b"abc", b"def"), struct.unpack("0s", b""))
print("index", struct.unpack("<H", struct.pack("<H", Index())))
print("bool", struct.unpack("??", struct.pack("??", [], [0])))
print("spaces", struct.pack(" <H".strip(), 258) == struct.pack("< H", 258))

for code in ("e", "f", "d"):
    for value in (0.0, -0.0, 1.0, -2.5, 0.333251953125, float("inf"), -float("inf")):
        packed = struct.pack(">" + code, value)
        restored = struct.unpack(">" + code, packed)[0]
        print("float", code, binascii.hexlify(packed), restored, math.copysign(1.0, restored))
    print("nan", code, math.isnan(struct.unpack("<" + code, struct.pack("<" + code, float("nan")))[0]))
print("half rounding", binascii.hexlify(struct.pack(">4e", 2 ** -24, 2 ** -25, 3 * 2 ** -25, 65504)))
print("subnormal", binascii.hexlify(struct.pack(">f", 2 ** -149)), binascii.hexlify(struct.pack(">d", 2 ** -1074)))
print("native overflow", math.isinf(struct.unpack("@f", struct.pack("@f", 1e300))[0]))

record = struct.Struct(b"<H2s")
buffer = bytearray(b"...........")
record.pack_into(buffer, 2, 258, b"hi")
record.pack_into(buffer, -4, 772, b"ok")
print("buffer", bytes(buffer), record.format, record.size, record.unpack_from(buffer, 2), record.unpack_from(buffer, -4))
print("iterator", list(record.iter_unpack(record.pack(1, b"ab") + record.pack(2, b"cd"))))
print("view", struct.unpack_from(">H", memoryview(b"abc"), 1))
for fmt in ("2", "<P", "=n", "1 H", "\x00", "z"):
    try:
        struct.calcsize(fmt)
    except struct.error:
        print("bad format", repr(fmt))
for fmt, value in (("b", 128), ("B", -1), ("H", 65536), ("q", 9223372036854775808), ("c", b"ab"), ("i", 1.5)):
    try:
        struct.pack(fmt, value)
    except struct.error:
        print("bad value", fmt)
try:
    struct.pack(">e", 65520)
except OverflowError:
    print("half overflow")
try:
    struct.iter_unpack("H", b"a")
except struct.error:
    print("iterator validates eagerly")

for plain in (b"", b"f", b"fo", b"foo", b"foob", bytes(range(256))):
    encoded = binascii.b2a_base64(plain, newline=False)
    print("base64", len(plain), encoded, binascii.a2b_base64(encoded, strict_mode=True) == plain)
    print("upstream", base64.b64decode(base64.b64encode(plain), validate=True) == plain,
          base64.b16decode(base64.b16encode(plain)) == plain)
    print("upstream codecs", base64.b32decode(base64.b32encode(plain)) == plain,
          base64.b32hexdecode(base64.b32hexencode(plain)) == plain,
          base64.a85decode(base64.a85encode(plain)) == plain,
          base64.b85decode(base64.b85encode(plain)) == plain)
print("non-strict", binascii.a2b_base64(b" Z=g=!=ignored"))
for bad in (b"=AAA", b"AAAA=", b"Zg===", b"Z=g=", b"Zg==\n", b"Zg$=", b"a", b"ab"):
    try:
        binascii.a2b_base64(bad, strict_mode=True)
    except binascii.Error as exc:
        print("base64 error", str(exc))
for group in (0, 1, 2, 3, -2, -3):
    print("hex grouping", group, binascii.hexlify(b"abcde", ":", group))
print("hex mixed", binascii.unhexlify("aBcD00"))
print("hex separator byte", binascii.hexlify(b"abc", b"\xff"), binascii.hexlify(b"abc", "\xff"))
for invalid in ("0", "gg", "a ", "zz"):
    try:
        binascii.unhexlify(invalid)
    except binascii.Error as exc:
        print("hex error", str(exc))
print("crc", binascii.crc32(b"123456789"), binascii.crc_hqx(b"123456789", 0), binascii.crc_hqx(b"123456789", 65535))
print("crc incremental", binascii.crc32(b"456789", binascii.crc32(b"123")), binascii.crc_hqx(b"456789", binascii.crc_hqx(b"123", 0)))
