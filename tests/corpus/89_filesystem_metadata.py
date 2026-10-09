# diaspore-caps: fs
"""Filesystem predicates, lexical paths, directory iteration and walk pruning."""

import os
from pathlib import Path

entry = Path(__file__)
parent = entry.parent
print(entry.exists(), entry.is_file(), entry.is_dir())
print(parent.exists(), parent.is_file(), parent.is_dir())
missing = parent / "__diaspore_missing_metadata_entry__"
print(missing.exists(), missing.is_file(), missing.is_dir())
print(Path("/dev/null").exists(), Path("/dev/null").is_file(), Path("/dev/null").is_dir())
print(os.path.exists(entry), os.path.isfile(entry), os.path.isdir(parent))
print(os.path.exists(missing), os.path.isfile(missing), os.path.isdir(missing))
print(os.path.isabs(os.getcwd()), str(Path.cwd()) == os.getcwd())
print(entry.name in os.listdir(parent), entry.name in [item.name for item in parent.iterdir()])
print(str(entry.resolve()) == os.path.realpath(entry))
print(os.path.realpath("/proc/self") != os.path.abspath("/proc/self"))
print(entry.absolute().is_absolute(), os.path.isabs(os.path.abspath(entry)))
print(str(Path("a/../b").absolute()).endswith("/a/../b"))
print(os.path.abspath("a/../b").endswith("/b"))

for value in ["", ".", "a//b/../c", "/..", "//a///b", "///a", "../../a"]:
    print(os.path.normpath(value))
for value in ["/a", "/", "//", "a/b///", "name", "/a/.name", "../a.tar.gz", "...name"]:
    print(os.path.split(value), os.path.splitext(value))
for value, start in [("/a/b/c", "/a"), ("/a", "/a/b/c"), ("/a", "/a"), ("/a/b", "/x/y")]:
    print(os.path.relpath(value, start))
print(os.path.join("/a", "b", "/c", "d"))
print(os.fspath(entry) == str(entry))

count = 0
for root, directories, files in os.walk(parent):
    print(os.fspath(root) == str(parent), entry.name in files)
    directories.clear()
    count += 1
print(count)

errors = []
print(list(os.walk(missing, onerror=lambda exc: errors.append(type(exc).__name__))))
print(errors)
