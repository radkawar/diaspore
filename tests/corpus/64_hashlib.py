"""Digest vectors, block boundaries and independent incremental copies."""

import hashlib

for constructor in [hashlib.sha256, hashlib.sha1, hashlib.md5]:
    for data in [b"", b"abc", b"message digest", b"a" * 55, b"a" * 56,
                 b"a" * 63, b"a" * 64, b"a" * 65, bytes(range(256)) * 4]:
        digest = constructor(data)
        print(digest.name, digest.digest_size, digest.block_size, digest.hexdigest())
        print(len(digest.digest()), digest.digest() == constructor(data).digest())
    original = constructor(b"prefix")
    copied = original.copy()
    print(original.update(b" one"))
    copied.update(b" two")
    print(original.hexdigest(), copied.hexdigest())
    before = original.hexdigest()
    print(before == original.hexdigest())
    original.update(b" more")
    print(original.hexdigest())
    data = b"long input with distinct bytes\x00\xff" * 130
    chunked = constructor()
    for index in range(0, len(data), 37):
        chunked.update(data[index:index + 37])
    print(chunked.hexdigest(), chunked.digest() == constructor(data).digest())

print(hashlib.new("SHA-256", b"abc").hexdigest())
print(hashlib.md5(b"abc", usedforsecurity=False).hexdigest())
try:
    hashlib.sha256("abc")
except TypeError as exc:
    print(str(exc))
