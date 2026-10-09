"""Provide deterministic OS helpers without filesystem or process access."""

name = "posix"
sep = "/"
altsep = None
extsep = "."
pathsep = ":"
linesep = "\n"
curdir = "."
pardir = ".."
devnull = "/dev/null"


class _Environ(dict):
    pass


environ = _Environ()


def getenv(key, default=None):
    return environ.get(key, default)


def getpid():
    return 1


def getcwd():
    return "/"


def fspath(path):
    return path


from . import path as path  # noqa: E402
