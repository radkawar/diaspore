"""SHA-3 and SHAKE vectors, sponge boundaries, snapshots and validation."""

import _sha3
import hashlib


class IndexOnly:
    def __index__(self):
        return 3


class IntLength(int):
    def __index__(self):
        raise AssertionError("integer payload must bypass overridden __index__")


class SecurityFlag:
    def __bool__(self):
        print("security flag evaluated")
        return False


class BadSecurityFlag:
    def __bool__(self):
        raise ValueError("security flag failure")


def failure(function, *args, **kwargs):
    try:
        function(*args, **kwargs)
    except (TypeError, ValueError, OverflowError, BufferError) as exc:
        print(type(exc).__name__, str(exc))
    else:
        print("unexpected success")


for name in ("sha3_224", "sha3_256", "sha3_384", "sha3_512", "shake_128", "shake_256"):
    constructor = getattr(_sha3, name)
    public = getattr(hashlib, name)
    prototype = constructor()
    rate = prototype.block_size
    shake = prototype.digest_size == 0
    print(name, prototype.name, rate, prototype.digest_size,
          prototype._rate_bits, prototype._capacity_bits, prototype._suffix.hex())
    for data in (b"", b"abc", bytes(range(256)),
                 b"a" * (rate - 1), b"a" * rate, b"a" * (rate + 1),
                 bytes(range(256)) * 2):
        digest = constructor(data)
        lengths = (0, 1, 32, rate - 1, rate, rate + 1, 2 * rate + 1) if shake else (None,)
        for length in lengths:
            arguments = () if length is None else (length,)
            raw = digest.digest(*arguments)
            hexed = digest.hexdigest(*arguments)
            print(len(data), length, hexed)
            print(raw.hex() == hexed, raw == digest.digest(*arguments),
                  raw == public(data).digest(*arguments),
                  raw == hashlib.new(name, data).digest(*arguments))

    data = bytes(range(256)) + b"incremental\x00\xff" * 7
    chunked = constructor()
    for start in range(0, len(data), 17):
        chunked.update(data[start:start + 17])
    args = (rate + 9,) if shake else ()
    print("chunked", chunked.hexdigest(*args),
          chunked.digest(*args) == constructor(data).digest(*args))
    original = constructor(b"p" * (rate - 1))
    copied = original.copy()
    print(type(copied) is constructor, original.update(b"one"), copied.update(b"two"))
    print(original.hexdigest(*args), copied.hexdigest(*args))
    before = original.digest(*args)
    print(before == original.copy().digest(*args), before == original.digest(*args))
    original.update(b"after digest")
    print(original.digest(*args) == constructor(b"p" * (rate - 1) + b"oneafter digest").digest(*args))

    mutable = bytearray(b"abc")
    from_buffer = constructor(mutable)
    mutable[0] = 120
    print("buffers", from_buffer.digest(*args) == constructor(b"abc").digest(*args),
          constructor(memoryview(mutable)).digest(*args) == constructor(b"xbc").digest(*args))
    from_buffer.update(memoryview(bytearray(b"def")))
    print(from_buffer.digest(*args) == constructor(b"abcdef").digest(*args))
    print(constructor(b"abc", usedforsecurity=SecurityFlag()).digest(*args) == constructor(b"abc").digest(*args))
    failure(constructor, b"abc", usedforsecurity=BadSecurityFlag())
    for bad in ("abc", None, 3, [1, 2], memoryview(b"abcdef")[::2]):
        failure(constructor, bad)
        failure(original.update, bad)
    print("failed update unchanged", original.digest(*args) == constructor(b"p" * (rate - 1) + b"oneafter digest").digest(*args))
    failure(constructor, data=b"abc")
    failure(constructor, string=b"abc")
    failure(constructor, b"abc", False)
    failure(constructor, b"abc", False, 0)
    failure(original.update)
    failure(original.update, b"a", b"b")
    failure(original.update, data=b"a")
    failure(original.copy, 1)
    failure(original.copy, extra=1)
    if shake:
        for length in (False, True, IntLength(3)):
            print("integer length", original.hexdigest(length))
        for length in (-1, 1 << 29, (1 << 64) - 1, 1 << 64,
                       None, 1.5, "3", IndexOnly()):
            failure(original.digest, length)
            failure(original.hexdigest, length)
        failure(original.digest)
        failure(original.hexdigest)
        failure(original.digest, length=1)
        failure(original.hexdigest, length=1)
        failure(original.digest, 1, 2)
    else:
        failure(original.digest, 1)
        failure(original.hexdigest, 1)
        failure(original.digest, length=1)
        failure(original.hexdigest, length=1)
    for attribute in ("name", "block_size", "digest_size", "_rate_bits", "_capacity_bits", "_suffix"):
        try:
            setattr(original, attribute, None)
        except AttributeError:
            print(attribute, "read-only")
