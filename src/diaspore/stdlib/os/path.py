"""Provide path manipulation using pure string operations."""

sep = "/"


def join(a, *parts):
    result = a
    for p in parts:
        if p.startswith("/"):
            result = p
        elif result == "" or result.endswith("/"):
            result += p
        else:
            result += "/" + p
    return result


def basename(p):
    return p.rsplit("/", 1)[-1]


def dirname(p):
    if "/" not in p:
        return ""
    return p.rsplit("/", 1)[0]


def split(p):
    return (dirname(p), basename(p))


def splitext(p):
    base = basename(p)
    if "." in base and not base.startswith("."):
        idx = p.rfind(".")
        return (p[:idx], p[idx:])
    return (p, "")


def normpath(p):
    parts = []
    for part in p.split("/"):
        if part == "" or part == ".":
            continue
        if part == ".." and parts and parts[-1] != "..":
            parts.pop()
        else:
            parts.append(part)
    prefix = "/" if p.startswith("/") else ""
    return prefix + "/".join(parts) or "."


def isabs(p):
    return p.startswith("/")


def abspath(p):
    return p if isabs(p) else normpath("/" + p)


def exists(p):
    return False
