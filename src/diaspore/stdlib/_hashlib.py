"""Hashlib's OpenSSL-facing API backed by portable digest primitives.

Only implemented algorithms are advertised. HMAC and PBKDF2 use RFC 2104
and RFC 8018 directly. Comparison does not short-circuit on differing bytes;
the interpreter does not provide a native constant-time execution guarantee.
"""

from _blake2 import blake2b as _blake2b
from _blake2 import blake2s as _blake2s
from _md5 import md5 as openssl_md5
from _operator import index as _index
from _sha1 import sha1 as openssl_sha1
from _sha2 import sha224 as openssl_sha224
from _sha2 import sha256 as openssl_sha256
from _sha2 import sha384 as openssl_sha384
from _sha2 import sha512 as openssl_sha512
from _sha3 import sha3_224 as _sha3_224
from _sha3 import sha3_256 as _sha3_256
from _sha3 import sha3_384 as _sha3_384
from _sha3 import sha3_512 as _sha3_512
from _sha3 import shake_128 as _shake_128
from _sha3 import shake_256 as _shake_256

from _hashcore import _buffer_bytes, _word_bytes


class UnsupportedDigestmodError(ValueError):
    """The requested digest is not available from this provider."""


class HASHXOF:
    """OpenSSL's extensible-output interface over a Keccak sponge."""

    def __new__(cls, *args, **kwargs):
        raise TypeError("cannot create '_hashlib.HASHXOF' instances")

    @property
    def name(self):
        return self._sponge.name

    @property
    def block_size(self):
        return self._sponge.block_size

    @property
    def digest_size(self):
        return 0

    def update(self, data, /):
        """Absorb another bytes-like input."""
        self._sponge.update(data)

    def copy(self):
        """Return an independent absorption state."""
        result = object.__new__(HASHXOF)
        result._sponge = self._sponge.copy()
        return result

    def digest(self, length):
        """Return length output bytes without changing the absorption state."""
        length = _xof_length(length)
        if length < 0:
            raise SystemError("Negative size passed to PyBytes_FromStringAndSize")
        return self._sponge._digest(length)

    def hexdigest(self, length):
        """Return length output bytes as hexadecimal."""
        length = _xof_length(length)
        if length < 0:
            raise MemoryError()
        return self._sponge._digest(length).hex()


def _xof_length(length):
    length = _index(length)
    if length < -9223372036854775808 or length > 9223372036854775807:
        raise OverflowError("Python int too large to convert to C ssize_t")
    return length


def openssl_sha3_224(string=b"", *, usedforsecurity=True):
    """Return a SHA3-224 digest."""
    return _sha3_224(string, usedforsecurity=usedforsecurity)


def openssl_sha3_256(string=b"", *, usedforsecurity=True):
    """Return a SHA3-256 digest."""
    return _sha3_256(string, usedforsecurity=usedforsecurity)


def openssl_sha3_384(string=b"", *, usedforsecurity=True):
    """Return a SHA3-384 digest."""
    return _sha3_384(string, usedforsecurity=usedforsecurity)


def openssl_sha3_512(string=b"", *, usedforsecurity=True):
    """Return a SHA3-512 digest."""
    return _sha3_512(string, usedforsecurity=usedforsecurity)


def openssl_shake_128(string=b"", *, usedforsecurity=True):
    """Return a SHAKE128 extensible-output digest."""
    result = object.__new__(HASHXOF)
    result._sponge = _shake_128(string, usedforsecurity=usedforsecurity)
    return result


def openssl_shake_256(string=b"", *, usedforsecurity=True):
    """Return a SHAKE256 extensible-output digest."""
    result = object.__new__(HASHXOF)
    result._sponge = _shake_256(string, usedforsecurity=usedforsecurity)
    return result


_CONSTRUCTORS = {
    "md5": openssl_md5,
    "sha1": openssl_sha1,
    "sha224": openssl_sha224,
    "sha256": openssl_sha256,
    "sha384": openssl_sha384,
    "sha512": openssl_sha512,
    "sha3_224": openssl_sha3_224,
    "sha3_256": openssl_sha3_256,
    "sha3_384": openssl_sha3_384,
    "sha3_512": openssl_sha3_512,
    "shake_128": openssl_shake_128,
    "shake_256": openssl_shake_256,
    "blake2b": _blake2b,
    "blake2s": _blake2s,
}
_ALIASES = {
    "md5": "md5",
    "ssl3-md5": "md5",
    "1.2.840.113549.2.5": "md5",
    "sha1": "sha1",
    "sha-1": "sha1",
    "ssl3-sha1": "sha1",
    "1.3.14.3.2.26": "sha1",
    "sha224": "sha224",
    "sha-224": "sha224",
    "2.16.840.1.101.3.4.2.4": "sha224",
    "sha256": "sha256",
    "sha-256": "sha256",
    "2.16.840.1.101.3.4.2.1": "sha256",
    "sha384": "sha384",
    "sha-384": "sha384",
    "2.16.840.1.101.3.4.2.2": "sha384",
    "sha512": "sha512",
    "sha-512": "sha512",
    "2.16.840.1.101.3.4.2.3": "sha512",
    "sha3-224": "sha3_224",
    "2.16.840.1.101.3.4.2.7": "sha3_224",
    "sha3-256": "sha3_256",
    "2.16.840.1.101.3.4.2.8": "sha3_256",
    "sha3-384": "sha3_384",
    "2.16.840.1.101.3.4.2.9": "sha3_384",
    "sha3-512": "sha3_512",
    "2.16.840.1.101.3.4.2.10": "sha3_512",
    "shake128": "shake_128",
    "shake-128": "shake_128",
    "2.16.840.1.101.3.4.2.11": "shake_128",
    "shake256": "shake_256",
    "shake-256": "shake_256",
    "2.16.840.1.101.3.4.2.12": "shake_256",
    "blake2b512": "blake2b",
    "blake2b-512": "blake2b",
    "1.3.6.1.4.1.1722.12.2.1.16": "blake2b",
    "blake2s256": "blake2s",
    "blake2s-256": "blake2s",
    "1.3.6.1.4.1.1722.12.2.2.8": "blake2s",
}
openssl_md_meth_names = frozenset(_CONSTRUCTORS)
_CONSTRUCTOR_NAMES = {
    openssl_md5: "md5",
    openssl_sha1: "sha1",
    openssl_sha224: "sha224",
    openssl_sha256: "sha256",
    openssl_sha384: "sha384",
    openssl_sha512: "sha512",
    openssl_sha3_224: "sha3_224",
    openssl_sha3_256: "sha3_256",
    openssl_sha3_384: "sha3_384",
    openssl_sha3_512: "sha3_512",
    openssl_shake_128: "shake_128",
    openssl_shake_256: "shake_256",
}
_MISSING = object()


def _constructor(name):
    if not isinstance(name, str):
        raise TypeError("name must be a string")
    if "\x00" in name:
        raise ValueError("embedded null character")
    constructor = _CONSTRUCTORS.get(name)
    if constructor is None:
        canonical = _ALIASES.get(str.lower(name))
        if canonical is not None:
            constructor = _CONSTRUCTORS[canonical]
    if constructor is None:
        raise UnsupportedDigestmodError("unsupported hash type " + name)
    return constructor


def new(name, string=b"", *, usedforsecurity=True):
    """Return a digest using a supported Python or OpenSSL algorithm name."""
    if not isinstance(name, str):
        raise TypeError("name must be a string")
    if "\x00" in name:
        raise ValueError("embedded null character")
    bool(usedforsecurity)
    data = _buffer_bytes(string)
    return _constructor(name)(data)


def _hmac_constructor(digestmod):
    if isinstance(digestmod, str):
        return _constructor(digestmod)
    name = _CONSTRUCTOR_NAMES.get(digestmod)
    if name is None:
        raise UnsupportedDigestmodError("Unsupported digestmod " + repr(digestmod))
    return _CONSTRUCTORS[name]


def _has_buffer(value):
    return isinstance(value, (bytes, bytearray, memoryview)) or hasattr(type(value), "__buffer__")


def _raw_buffer(value):
    if not _has_buffer(value):
        raise TypeError("a bytes-like object is required, not '" + type(value).__name__ + "'")
    return _buffer_bytes(value)


class HMAC:
    """Incremental RFC 2104 HMAC with independently copyable digest states."""

    def __new__(cls, *args, **kwargs):
        raise TypeError("cannot create '_hashlib.HMAC' instances")

    @property
    def name(self):
        return "hmac-" + self._inner.name

    @property
    def digest_size(self):
        return self._inner.digest_size

    @property
    def block_size(self):
        return self._inner.block_size

    def update(self, msg, /):
        """Feed a bytes-like object into the authenticated message."""
        self._inner.update(msg)

    def copy(self):
        """Return an independent copy without reprocessing the key."""
        result = object.__new__(HMAC)
        result._inner = self._inner.copy()
        result._outer = self._outer.copy()
        return result

    def digest(self):
        """Return the authentication tag without consuming the state."""
        outer = self._outer.copy()
        outer.update(self._inner.digest())
        return outer.digest()

    def hexdigest(self):
        """Return the hexadecimal authentication tag."""
        return "".join([f"{value:02x}" for value in self.digest()])


def _new_hmac(key, constructor):
    inner = constructor()
    if inner.digest_size == 0:
        raise ValueError("no reason supplied")
    size = inner.block_size
    if len(key) > size:
        key = constructor(key).digest()
    key += bytes(size - len(key))
    result = object.__new__(HMAC)
    result._inner = inner
    result._outer = constructor()
    result._inner.update(bytes([value ^ 0x36 for value in key]))
    result._outer.update(bytes([value ^ 0x5C for value in key]))
    return result


def hmac_new(key, msg=b"", digestmod=_MISSING):
    """Return a new HMAC using an implemented fixed-length digest."""
    key = _raw_buffer(key)
    if len(key) > 2147483647:
        raise OverflowError("key is too long.")
    if digestmod is _MISSING:
        raise TypeError("Missing required parameter 'digestmod'.")
    result = _new_hmac(key, _hmac_constructor(digestmod))
    if msg is not None:
        result.update(msg)
    return result


def hmac_digest(key, msg, digest):
    """Return a single-shot HMAC for bytes-like key and message inputs."""
    key = _raw_buffer(key)
    msg = _raw_buffer(msg)
    if len(key) > 2147483647:
        raise OverflowError("key is too long.")
    if len(msg) > 2147483647:
        raise OverflowError("msg is too long.")
    result = _new_hmac(key, _hmac_constructor(digest))
    result.update(msg)
    return result.digest()


def compare_digest(a, b, /):
    """Compare ASCII strings or buffers without content-dependent early exit."""
    if isinstance(a, str) and isinstance(b, str):
        if not str.isascii(a) or not str.isascii(b):
            raise TypeError("comparing strings with non-ASCII characters is not supported")
        a = str.encode(a, "ascii")
        b = str.encode(b, "ascii")
    else:
        if not _has_buffer(a) and not _has_buffer(b):
            raise TypeError(
                "unsupported operand types(s) or combination of types: '"
                + type(a).__name__[:100]
                + "' and '"
                + type(b).__name__[:100]
                + "'"
            )
        a = _raw_buffer(a)
        b = _raw_buffer(b)
    different = 0
    if len(a) != len(b):
        a = b
        different = 1
    for i in range(len(b)):
        different |= a[i] ^ b[i]
    return different == 0


def _long(value):
    value = _index(value)
    if value < -9223372036854775808 or value > 9223372036854775807:
        raise OverflowError("Python int too large to convert to C long")
    return value


def pbkdf2_hmac(hash_name, password, salt, iterations, dklen=None):
    """Derive a key with PBKDF2-HMAC as specified by RFC 8018."""
    constructor = _constructor(hash_name)
    password = _raw_buffer(password)
    salt = _raw_buffer(salt)
    iterations = _long(iterations)
    if len(password) > 2147483647:
        raise OverflowError("password is too long.")
    if len(salt) > 2147483647:
        raise OverflowError("salt is too long.")
    if iterations < 1:
        raise ValueError("iteration value must be greater than 0.")
    if iterations > 2147483647:
        raise OverflowError("iteration value is too great.")
    template = _new_hmac(password, constructor)
    size = template.digest_size
    dklen = size if dklen is None else _long(dklen)
    if dklen < 1:
        raise ValueError("key length must be greater than 0.")
    if dklen > 2147483647:
        raise OverflowError("key length is too great.")
    output = []
    for block in range(1, (dklen + size - 1) // size + 1):
        current = template.copy()
        current.update(salt)
        current.update(_word_bytes(block, 4))
        previous = current.digest()
        accumulated = int.from_bytes(previous, "big")
        for _iteration in range(1, iterations):
            current = template.copy()
            current.update(previous)
            previous = current.digest()
            accumulated ^= int.from_bytes(previous, "big")
        output.extend(_word_bytes(accumulated, size))
    return bytes(output[:dklen])
