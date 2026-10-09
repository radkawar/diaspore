"""Provide POSIX path helpers and capability-controlled filesystem metadata."""

import _os

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
    return _os.getcwd()


def fspath(path):
    if isinstance(path, (str, bytes)):
        return path
    if not hasattr(path, "__fspath__"):
        raise TypeError("expected str, bytes or os.PathLike object")
    value = path.__fspath__()
    if not isinstance(value, (str, bytes)):
        raise TypeError("__fspath__ must return str or bytes")
    return value


from . import path as path  # noqa: E402


def listdir(path="."):
    return _os.listdir(fspath(path))


def walk(top, topdown=True, onerror=None, followlinks=False):
    """Walk directories, allowing callers to prune top-down directory lists."""
    top = fspath(top)
    try:
        names = listdir(top)
    except OSError as error:
        if onerror is not None:
            onerror(error)
        return
    directories = []
    files = []
    for name in names:
        child = path.join(top, name)
        try:
            directory = path.isdir(child)
        except OSError:
            directory = False
        if directory:
            directories.append(name)
        else:
            files.append(name)
    if topdown:
        yield top, directories, files
    resolved_top = path.realpath(top) if directories and not followlinks else None
    for name in directories:
        child = path.join(top, name)
        if followlinks or path.realpath(child) == path.join(resolved_top, name):
            yield from walk(child, topdown, onerror, followlinks)
    if not topdown:
        yield top, directories, files
