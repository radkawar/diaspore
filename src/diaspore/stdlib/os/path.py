"""Provide POSIX string paths and capability-controlled metadata predicates.

Filesystem lookup, current-directory access and symlink resolution use the
private native _os bridge. Byte paths and strict realpath resolution are not
supported. Lexical operations do not access the filesystem.
"""

from os import fspath

import _os

sep = "/"


def _path(value):
    value = fspath(value)
    if not isinstance(value, str):
        raise NotImplementedError("byte paths are unsupported")
    return value


def join(a, *parts):
    result = _path(a)
    for item in parts:
        part = _path(item)
        if part.startswith("/"):
            result = part
        elif result == "" or result.endswith("/"):
            result += part
        else:
            result += "/" + part
    return result


def basename(p):
    return _path(p).rsplit("/", 1)[-1]


def dirname(p):
    p = _path(p)
    end = p.rfind("/") + 1
    result = p[:end]
    if result and result != "/" * len(result):
        result = result.rstrip("/")
    return result


def split(p):
    p = _path(p)
    return dirname(p), basename(p)


def splitext(p):
    p = _path(p)
    start = p.rfind("/") + 1
    dot = p.rfind(".")
    if dot >= start and any(char != "." for char in p[start:dot]):
        return p[:dot], p[dot:]
    return p, ""


def normpath(p):
    p = _path(p)
    prefix = ""
    if p.startswith("/"):
        prefix = "//" if p.startswith("//") and not p.startswith("///") else "/"
    parts = []
    for part in p.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if parts and parts[-1] != "..":
                parts.pop()
            elif not prefix:
                parts.append(part)
        else:
            parts.append(part)
    return prefix + "/".join(parts) or "."


def isabs(p):
    return _path(p).startswith("/")


def abspath(p):
    p = _path(p)
    if not isabs(p):
        p = join(_os.getcwd(), p)
    return normpath(p)


def realpath(p, *, strict=False):
    if strict:
        raise NotImplementedError("strict realpath resolution is unsupported")
    return _os.realpath(_path(p))


def relpath(p, start=None):
    p = _path(p)
    if not p:
        raise ValueError("no path specified")
    if start is None:
        start = _os.getcwd()
    destination = [part for part in abspath(p).split("/") if part]
    origin = [part for part in abspath(start).split("/") if part]
    common = 0
    while common < min(len(destination), len(origin)) and destination[common] == origin[common]:
        common += 1
    parts = [".."] * (len(origin) - common) + destination[common:]
    return "/".join(parts) or "."


def exists(p):
    return _os.kind(_path(p)) is not None


def isfile(p):
    return _os.kind(_path(p)) == "file"


def isdir(p):
    return _os.kind(_path(p)) == "dir"
