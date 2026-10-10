"""MD5 constructor for hashlib."""

from _hashcore import _Hash


def md5(string=b"", *, usedforsecurity=True):
    """Return an MD5 hash initialized from a bytes-like object."""
    bool(usedforsecurity)
    return _Hash("md5", string)
