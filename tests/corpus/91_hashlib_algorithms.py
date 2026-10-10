"""CPython-oracle hashes, authenticated messages and key derivation."""

import hashlib
import hmac

for name in ("md5", "sha1", "sha224", "sha256", "sha384", "sha512", "blake2b", "blake2s"):
    constructor = getattr(hashlib, name)
    for length in (0, 3, 55, 56, 63, 64, 65, 111, 112, 127, 128, 129, 257):
        data = bytes(range(256))[:length] if length < 256 else bytes(range(256)) + b"z"
        direct = constructor(data)
        incremental = constructor()
        for offset in range(0, len(data), 17):
            incremental.update(data[offset:offset + 17])
        print(name, length, direct.digest_size, direct.block_size, direct.hexdigest())
        print(direct.digest() == incremental.digest(), direct.digest() == direct.copy().digest())
    value = constructor(bytearray(b"abc"))
    print(value.update(memoryview(b"def")), value.hexdigest())
    copy = value.copy()
    value.update(b"left")
    copy.update(b"right")
    print(value.hexdigest(), copy.hexdigest())
    print(constructor(b"abc", usedforsecurity=False).hexdigest())
    print(hashlib.new(name, b"abc").hexdigest())
    for invalid in ("abc", 3, [1, 2, 3], memoryview(b"abcdef")[::2]):
        before = value.digest()
        try:
            value.update(invalid)
        except (TypeError, BufferError) as exc:
            print(type(exc).__name__, before == value.digest())
    try:
        value.digest_size = 1
    except AttributeError:
        print("readonly")

for constructor in (hashlib.blake2b, hashlib.blake2s):
    for size in (1, 17, constructor.MAX_DIGEST_SIZE):
        value = constructor(b"abc", digest_size=size, key=b"secret", salt=b"salt", person=b"person")
        print(value.name, size, value.hexdigest())
        copy = value.copy()
        copy.update(b"tail")
        print(copy.hexdigest(), value.hexdigest())
    for last in (False, True):
        value = constructor(bytes(range(256)), fanout=2, depth=3, leaf_size=128,
                            node_offset=2**40 + 3, node_depth=1, inner_size=16, last_node=last)
        print(value.name, last, value.hexdigest())
    for length in (0, 64, 128, 129):
        data = b"k" * length
        keyed = constructor(key=bytes(range(constructor.MAX_KEY_SIZE)))
        keyed.update(data[:64])
        keyed.update(data[64:])
        print(keyed.hexdigest())
    for options in ({"digest_size": 0}, {"digest_size": constructor.MAX_DIGEST_SIZE + 1},
                    {"key": b"a" * (constructor.MAX_KEY_SIZE + 1)},
                    {"salt": b"a" * (constructor.SALT_SIZE + 1)},
                    {"person": b"a" * (constructor.PERSON_SIZE + 1)},
                    {"depth": 0}, {"fanout": 256}, {"node_depth": -1},
                    {"inner_size": constructor.MAX_DIGEST_SIZE + 1}):
        try:
            constructor(**options)
        except (TypeError, ValueError, OverflowError) as exc:
            print(type(exc).__name__)

for alias in ("SHA-256", "SHA256", "sha-512", "SHA3-256", "blake2b512", "blake2s256"):
    value = hashlib.new(alias, b"abc")
    print(alias, value.name, value.hexdigest())

for name in ("md5", "sha1", "sha224", "sha256", "sha384", "sha512", "sha3_256", "blake2b"):
    constructor = getattr(hashlib, name)
    for key in (b"", b"secret", bytes(range(256))):
        value = hmac.new(key, b"message", name)
        print(value.name, value.digest_size, value.block_size, value.hexdigest())
        print(value.digest() == hmac.digest(key, b"message", name),
              value.digest() == hmac.digest(key, b"message", constructor))
        copy = value.copy()
        value.update(b"left")
        copy.update(b"right")
        print(value.hexdigest(), copy.hexdigest())
    print(hmac.new(bytearray(b"key"), memoryview(b"data"), constructor).hexdigest())

def custom_sha256(data=b""):
    return hashlib.sha256(data)

print(hmac.new(b"key", b"data", custom_sha256).hexdigest())
print(hmac.digest(b"key", b"data", custom_sha256).hex())
for a, b in ((b"", b""), (b"abc", b"abc"), (b"abc", b"abd"), (b"abc", b"ab"),
             ("abc", "abc"), ("abc", "abd"), (bytearray(b"abc"), memoryview(b"abc"))):
    print(hmac.compare_digest(a, b))
for a, b in (("caf\u00e9", "caf\u00e9"), ("\ud800", "\ud800"), ("abc", b"abc"),
             (None, 3), (b"abc", memoryview(b"abcdef")[::2])):
    try:
        hmac.compare_digest(a, b)
    except (TypeError, BufferError) as exc:
        print(type(exc).__name__, str(exc))

for name in ("sha1", "sha256", "sha512", "sha3_256"):
    for iterations in (1, 2, 7):
        for size in (None, 1, 40):
            print(name, iterations, size,
                  hashlib.pbkdf2_hmac(name, b"password\x00", b"salt\x00", iterations, size).hex())
print(hashlib.pbkdf2_hmac("SHA-256", bytearray(b"password"), memoryview(b"salt"), 2, 32).hex())
for iterations, size in ((0, 16), (-1, 16), (2**31, 16), (1, 0), (1, -1), (1, 2**31)):
    try:
        hashlib.pbkdf2_hmac("sha256", b"password", b"salt", iterations, size)
    except (ValueError, OverflowError) as exc:
        print(type(exc).__name__, str(exc))

class OutputSize:
    def __index__(self):
        return 19

for constructor in (hashlib.shake_128, hashlib.shake_256):
    value = constructor(string=b"abc")
    print(value.name, value.digest(OutputSize()).hex(), value.hexdigest(length=19))
    copied = value.copy()
    value.update(b"left")
    copied.update(b"right")
    print(value.hexdigest(20), copied.hexdigest(20))
print(hashlib.sha3_256(string=b"abc").hexdigest())
try:
    hashlib.new("not-a-supported-digest", b"abc")
except ValueError:
    print("unsupported digest")
