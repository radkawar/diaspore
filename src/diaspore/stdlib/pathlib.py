"""Provide POSIX lexical paths, file I/O and capability-controlled metadata.

Path and PurePath select their POSIX variants. Joining, comparisons, parts,
parents, suffixes, relative paths, and name replacements are lexical: '..'
is preserved and no filesystem lookup occurs. File I/O supports the runtime's
r/w/a/rb/wb modes and strict UTF-8 text. Predicates, directory listing, current
directory and symlink resolution use the filesystem capability. Mutation,
Windows paths, glob matching and URI conversion raise NotImplementedError.
Paths accept strings and string-returning __fspath__.
"""

import os


def _path_string(value):
    if isinstance(value, str):
        return value
    if isinstance(value, PurePosixPath):
        return str(value)
    if hasattr(value, "__fspath__"):
        value = value.__fspath__()
        if isinstance(value, str):
            return value
    raise TypeError(
        "argument should be a str or an os.PathLike object where __fspath__ returns a str, not '"
        + type(value).__name__ + "'"
    )


def _parse_path(values):
    path = ""
    for value in values:
        part = _path_string(value)
        if part.startswith("/"):
            path = part
        elif part:
            path = path + "/" + part if path else part
    root = ""
    if path.startswith("/"):
        root = "//" if path.startswith("//") and not path.startswith("///") else "/"
    parts = [part for part in path.split("/") if part and part != "."]
    return root, tuple(parts)


class _Parents:
    def __init__(self, path):
        self._path = path

    def __len__(self):
        return len(self._path._parts)

    def __getitem__(self, index):
        try:
            selected = range(len(self))[index]
        except IndexError:
            raise IndexError(index) from None
        if not isinstance(selected, int):
            return tuple(self[i] for i in selected)
        path = self._path
        return type(path)(path._root + "/".join(path._parts[:len(self) - selected - 1]))

    def __iter__(self):
        for index in range(len(self)):
            yield self[index]

    def __repr__(self):
        return "<" + type(self._path).__name__ + ".parents>"


class PurePosixPath:
    """Represent a normalized POSIX path without accessing the filesystem."""

    def __init__(self, *pathsegments):
        self._root, self._parts = _parse_path(pathsegments)

    def __str__(self):
        return self._root + "/".join(self._parts) or "."

    def __repr__(self):
        return type(self).__name__ + "(" + repr(str(self)) + ")"

    def __fspath__(self):
        return str(self)

    def __bytes__(self):
        return str(self).encode("utf-8")

    def __hash__(self):
        return hash(self.parts)

    def __eq__(self, other):
        if not isinstance(other, PurePosixPath):
            return NotImplemented
        return self.parts == other.parts

    def __lt__(self, other):
        if not isinstance(other, PurePosixPath):
            return NotImplemented
        return self.parts < other.parts

    def __le__(self, other):
        if not isinstance(other, PurePosixPath):
            return NotImplemented
        return self.parts <= other.parts

    def __gt__(self, other):
        if not isinstance(other, PurePosixPath):
            return NotImplemented
        return self.parts > other.parts

    def __ge__(self, other):
        if not isinstance(other, PurePosixPath):
            return NotImplemented
        return self.parts >= other.parts

    def __truediv__(self, other):
        try:
            return self.joinpath(other)
        except TypeError:
            return NotImplemented

    def __rtruediv__(self, other):
        try:
            return type(self)(other, self)
        except TypeError:
            return NotImplemented

    @property
    def parts(self):
        return (self._root,) + self._parts if self._root else self._parts

    @property
    def drive(self):
        return ""

    @property
    def root(self):
        return self._root

    @property
    def anchor(self):
        return self._root

    @property
    def name(self):
        return self._parts[-1] if self._parts else ""

    @property
    def suffix(self):
        name = self.name
        index = name.rfind(".")
        return name[index:] if 0 < index < len(name) - 1 else ""

    @property
    def suffixes(self):
        name = self.name
        if name.endswith("."):
            return []
        return ["." + part for part in name.lstrip(".").split(".")[1:]]

    @property
    def stem(self):
        suffix = self.suffix
        return self.name[:-len(suffix)] if suffix else self.name

    @property
    def parent(self):
        if not self._parts:
            return self
        return type(self)(self._root + "/".join(self._parts[:-1]))

    @property
    def parents(self):
        return _Parents(self)

    def as_posix(self):
        return str(self)

    def is_absolute(self):
        return bool(self._root)

    def is_reserved(self):
        return False

    def joinpath(self, *pathsegments):
        return type(self)(self, *pathsegments)

    def with_name(self, name):
        if not self.name:
            raise ValueError(repr(self) + " has an empty name")
        if not isinstance(name, str):
            raise TypeError("name must be a string")
        if not name or name == "." or "/" in name:
            raise ValueError("Invalid name " + repr(name))
        return self.parent / name

    def with_stem(self, stem):
        return self.with_name(stem + self.suffix)

    def with_suffix(self, suffix):
        if not isinstance(suffix, str):
            raise TypeError("suffix must be a string")
        if "/" in suffix or (suffix and (not suffix.startswith(".") or suffix == ".")):
            raise ValueError("Invalid suffix " + repr(suffix))
        if not self.name:
            raise ValueError(repr(self) + " has an empty name")
        return self.with_name(self.stem + suffix)

    def relative_to(self, *other, walk_up=False):
        if walk_up:
            raise NotImplementedError("relative_to(walk_up=True) is unsupported")
        if not other:
            raise TypeError("need at least one argument")
        base = PurePosixPath(*other)
        count = len(base._parts)
        if self._root != base._root or self._parts[:count] != base._parts:
            raise ValueError(
                repr(str(self)) + " is not in the subpath of " + repr(str(base))
                + " OR one path is relative and the other is absolute."
            )
        return type(self)(*self._parts[count:])

    def is_relative_to(self, *other):
        try:
            self.relative_to(*other)
            return True
        except ValueError:
            return False

    def _unsupported(self, *args, **kwargs):
        raise NotImplementedError("this pathlib operation is unsupported")

    match = _unsupported
    full_match = _unsupported
    as_uri = _unsupported
    from_uri = _unsupported


class PosixPath(PurePosixPath):
    """Add capability-controlled file I/O to lexical POSIX paths."""

    def open(self, mode="r", buffering=-1, encoding=None, errors=None, newline=None):
        if mode not in ("r", "w", "a", "rb", "wb"):
            raise NotImplementedError("unsupported file mode")
        if buffering != -1 or newline is not None:
            raise NotImplementedError("buffering and newline overrides are unsupported")
        if "b" in mode:
            if encoding is not None:
                raise ValueError("binary mode doesn't take an encoding argument")
            if errors is not None:
                raise ValueError("binary mode doesn't take an errors argument")
        else:
            _text_options(encoding, errors)
        return open(str(self), mode)

    def read_bytes(self):
        with self.open("rb") as stream:
            return stream.read()

    def write_bytes(self, data):
        if not isinstance(data, bytes):
            raise TypeError("a bytes-like object is required, not '" + type(data).__name__ + "'")
        with self.open("wb") as stream:
            return stream.write(data)

    def read_text(self, encoding=None, errors=None):
        _text_options(encoding, errors)
        return self.read_bytes().decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")

    def write_text(self, data, encoding=None, errors=None, newline=None):
        if not isinstance(data, str):
            raise TypeError("data must be str, not " + type(data).__name__)
        _text_options(encoding, errors)
        if newline not in (None, "", "\n", "\r", "\r\n"):
            raise ValueError("illegal newline value: " + repr(newline))
        translated = data if newline in (None, "", "\n") else data.replace("\n", newline)
        self.write_bytes(translated.encode("utf-8"))
        return len(data)

    @classmethod
    def cwd(cls):
        return cls(os.getcwd())

    @classmethod
    def home(cls):
        raise NotImplementedError("home-directory lookup is unsupported")

    def _unsupported(self, *args, **kwargs):
        raise NotImplementedError("this filesystem operation is unsupported")

    def absolute(self):
        return self if self.is_absolute() else type(self)(os.getcwd(), self)

    def resolve(self, strict=False):
        return type(self)(os.path.realpath(self, strict=strict))

    def exists(self, *, follow_symlinks=True):
        if not follow_symlinks:
            raise NotImplementedError("symlink metadata is unsupported")
        return os.path.exists(self)

    def is_file(self, *, follow_symlinks=True):
        if not follow_symlinks:
            raise NotImplementedError("symlink metadata is unsupported")
        return os.path.isfile(self)

    def is_dir(self, *, follow_symlinks=True):
        if not follow_symlinks:
            raise NotImplementedError("symlink metadata is unsupported")
        return os.path.isdir(self)

    def iterdir(self):
        for name in os.listdir(self):
            yield self / name

    expanduser = _unsupported
    stat = _unsupported
    lstat = _unsupported
    is_symlink = _unsupported
    is_mount = _unsupported
    is_socket = _unsupported
    is_fifo = _unsupported
    is_block_device = _unsupported
    is_char_device = _unsupported
    owner = _unsupported
    group = _unsupported
    samefile = _unsupported
    glob = _unsupported
    rglob = _unsupported
    walk = _unsupported
    mkdir = _unsupported
    rmdir = _unsupported
    unlink = _unsupported
    rename = _unsupported
    replace = _unsupported
    touch = _unsupported
    chmod = _unsupported
    lchmod = _unsupported
    symlink_to = _unsupported
    hardlink_to = _unsupported
    link_to = _unsupported
    readlink = _unsupported


def _text_options(encoding, errors):
    if encoding is not None and encoding.lower().replace("_", "-") not in ("utf-8", "utf8"):
        raise NotImplementedError("only UTF-8 text is supported")
    if errors not in (None, "strict"):
        raise NotImplementedError("only strict text error handling is supported")


class PureWindowsPath:
    """Reject Windows paths rather than applying POSIX rules to them."""

    def __init__(self, *args, **kwargs):
        raise NotImplementedError("Windows paths are unsupported")


WindowsPath = PureWindowsPath
PurePath = PurePosixPath
Path = PosixPath
