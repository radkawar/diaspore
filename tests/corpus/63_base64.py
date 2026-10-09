"""Base64 variants, strict decoding and base16 byte conversion."""

import base64
import binascii

for data in [b"", b"f", b"fo", b"foo", b"foob", b"fooba", b"foobar", bytes(range(256))]:
    encoded = base64.b64encode(data)
    print(encoded, base64.b64decode(encoded) == data)
    url = base64.urlsafe_b64encode(data)
    print(url, base64.urlsafe_b64decode(url) == data)
    print(base64.b64decode(base64.b64encode(data, b"-_"), b"-_") == data)

print(base64.b64decode(" Zm9v\n!"))
print(base64.b64decode("Zm9v", validate=True))
print(base64.b64decode("Zg===="))
print(base64.encodebytes(b"abc" * 25))
print(base64.decodebytes(base64.encodebytes(b"abc" * 25)))
print(base64.b16encode(b"\x00\xab\xff"))
print(base64.b16decode("00ABFF"), base64.b16decode("00abff", casefold=True))
print(binascii.hexlify(b"\x00\xab\xff"), binascii.unhexlify("00abff"))
for value in ["a", "ab", "abc", "=abc", "a=bc", "ab=c", "ab==c", "!abc", "abc\u00e9"]:
    try:
        base64.b64decode(value, validate=True)
    except ValueError as exc:
        print(type(exc).__name__, str(exc))
