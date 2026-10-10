"""SHA-1 constructor for hashlib."""

from _hashcore import _Hash


def sha1(string=b"", *, usedforsecurity=True):
    """Return a SHA-1 hash initialized from a bytes-like object."""
    bool(usedforsecurity)
    return _Hash("sha1", string)
