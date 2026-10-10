"""SHA-224, SHA-256, SHA-384 and SHA-512 constructors for hashlib."""

from _hashcore import _Hash


def sha224(string=b"", *, usedforsecurity=True):
    """Return a SHA-224 hash initialized from a bytes-like object."""
    bool(usedforsecurity)
    return _Hash("sha224", string)


def sha256(string=b"", *, usedforsecurity=True):
    """Return a SHA-256 hash initialized from a bytes-like object."""
    bool(usedforsecurity)
    return _Hash("sha256", string)


def sha384(string=b"", *, usedforsecurity=True):
    """Return a SHA-384 hash initialized from a bytes-like object."""
    bool(usedforsecurity)
    return _Hash("sha384", string)


def sha512(string=b"", *, usedforsecurity=True):
    """Return a SHA-512 hash initialized from a bytes-like object."""
    bool(usedforsecurity)
    return _Hash("sha512", string)
