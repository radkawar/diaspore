"""POSIX interfaces backed exclusively by sandbox filesystem capabilities.

Paths and metadata pass through the private _os bridge. Operations lacking a
native capability-checked primitive raise NotImplementedError; this module
never opens host files or fabricates metadata. Environment state belongs to
this module instance and is never inherited from or written to the host.
"""

import sys as _sys

import _os

error = OSError
F_OK = 0
X_OK = 1
W_OK = 2
R_OK = 4
SEEK_SET = 0
SEEK_CUR = 1
SEEK_END = 2
# A native descriptor provider owns its flag ABI; other bridges use Linux bits.
O_RDONLY = getattr(_os, "O_RDONLY", 0)
O_WRONLY = getattr(_os, "O_WRONLY", 1)
O_RDWR = getattr(_os, "O_RDWR", 2)
O_CREAT = getattr(_os, "O_CREAT", 64)
O_EXCL = getattr(_os, "O_EXCL", 128)
O_TRUNC = getattr(_os, "O_TRUNC", 512)
O_APPEND = getattr(_os, "O_APPEND", 1024)
O_NONBLOCK = getattr(_os, "O_NONBLOCK", 2048)
O_DIRECTORY = getattr(_os, "O_DIRECTORY", 65536)
O_NOFOLLOW = getattr(_os, "O_NOFOLLOW", 131072)
O_CLOEXEC = getattr(_os, "O_CLOEXEC", 524288)
O_SYNC = getattr(_os, "O_SYNC", 1052672)

# These flags advertise only operations the bridge actually implements.
_have_functions = ["HAVE_LSTAT"] if hasattr(_os, "lstat") else []
environ = {}
_environment = {}


def _primitive(name):
    function = getattr(_os, name, None)
    if function is None:
        raise NotImplementedError("posix." + name + " is unavailable in this runtime")
    return function


def _integer(value):
    if isinstance(value, int):
        return value
    method = getattr(type(value), "__index__", None)
    if method is None:
        raise TypeError("'" + type(value).__name__ + "' object cannot be interpreted as an integer")
    value = method(value)
    if not isinstance(value, int):
        raise TypeError("__index__ returned non-int")
    return value


def fspath(path):
    """Return a str or bytes path using the type's __fspath__ protocol."""
    if isinstance(path, (str, bytes)):
        return path
    method = getattr(type(path), "__fspath__", None)
    if method is None:
        raise TypeError("expected str, bytes or os.PathLike object, not " + type(path).__name__)
    result = method(path)
    if not isinstance(result, (str, bytes)):
        raise TypeError(
            "expected "
            + type(path).__name__
            + ".__fspath__() to return str or bytes, not "
            + type(result).__name__
        )
    return result


def fsencode(filename):
    """Encode a path using the runtime filesystem encoding and error handler."""
    filename = fspath(filename)
    if isinstance(filename, bytes):
        return filename
    return filename.encode(_sys.getfilesystemencoding(), _sys.getfilesystemencodeerrors())


def fsdecode(filename):
    """Decode a path using the runtime filesystem encoding and error handler."""
    filename = fspath(filename)
    if isinstance(filename, str):
        return filename
    return filename.decode(_sys.getfilesystemencoding(), _sys.getfilesystemencodeerrors())


def _path(path):
    path = fspath(path)
    if isinstance(path, bytes):
        # Native bridges currently accept Unicode paths only.
        raise NotImplementedError("byte filesystem paths are unsupported")
    if "\x00" in path:
        raise ValueError("embedded null byte")
    return path


def getcwd():
    """Return the current directory through the filesystem capability."""
    return _os.getcwd()


def getcwdb():
    """Return the encoded current directory through the filesystem capability."""
    return fsencode(getcwd())


def listdir(path=None):
    """List a capability-authorized directory without dot entries."""
    if path is None:
        path = "."
    return _os.listdir(_path(path))


_stat_names = (
    "st_mode",
    "st_ino",
    "st_dev",
    "st_nlink",
    "st_uid",
    "st_gid",
    "st_size",
    "st_atime",
    "st_mtime",
    "st_ctime",
)
_stat_extra = (
    "st_atime",
    "st_mtime",
    "st_ctime",
    "st_atime_ns",
    "st_mtime_ns",
    "st_ctime_ns",
    "st_blksize",
    "st_blocks",
    "st_rdev",
)
_stat_default = object()
_dict_type = dict


class stat_result(tuple):
    """Immutable ten-element stat sequence with named timestamp metadata."""

    n_sequence_fields = 10
    n_unnamed_fields = 3
    n_fields = 19

    def __new__(cls, sequence, dict=_stat_default):
        """Create a stat sequence with optional supplemental named fields."""
        values = tuple(sequence)
        if dict is not _stat_default and not isinstance(dict, _dict_type):
            raise TypeError("os.stat_result() takes a dict as second arg, if any")
        size = len(values)
        if size < 10:
            raise TypeError(
                "os.stat_result() takes an at least 10-sequence (" + str(size) + "-sequence given)"
            )
        if size > 19:
            raise TypeError(
                "os.stat_result() takes an at most 19-sequence (" + str(size) + "-sequence given)"
            )
        extra = []
        for index, name in enumerate(_stat_extra):
            if index + 10 < size:
                value = values[index + 10]
            else:
                value = None if dict is _stat_default else dict.get(name)
            if value is None and index < 3:
                value = values[index + 7]
            extra.append(value)
        result = tuple.__new__(cls, values[:10])
        object.__setattr__(result, "_extra", tuple(extra))
        return result

    def __setattr__(self, name, value):
        """Reject changes to immutable stat metadata."""
        raise AttributeError("readonly attribute")

    def __delattr__(self, name):
        """Reject deletion of immutable stat metadata."""
        raise AttributeError("readonly attribute")

    def __repr__(self):
        """Return the named stat fields in constructor-style form."""
        return (
            "os.stat_result("
            + ", ".join(name + "=" + repr(self[index]) for index, name in enumerate(_stat_names))
            + ")"
        )

    def __reduce__(self):
        """Return the constructor arguments preserving supplemental fields."""
        return stat_result, (
            tuple(self),
            {name: self._extra[index] for index, name in enumerate(_stat_extra)},
        )

    st_mode = property(lambda self: self[0])
    st_ino = property(lambda self: self[1])
    st_dev = property(lambda self: self[2])
    st_nlink = property(lambda self: self[3])
    st_uid = property(lambda self: self[4])
    st_gid = property(lambda self: self[5])
    st_size = property(lambda self: self[6])
    st_atime = property(lambda self: self._extra[0])
    st_mtime = property(lambda self: self._extra[1])
    st_ctime = property(lambda self: self._extra[2])
    st_atime_ns = property(lambda self: self._extra[3])
    st_mtime_ns = property(lambda self: self._extra[4])
    st_ctime_ns = property(lambda self: self._extra[5])
    st_blksize = property(lambda self: self._extra[6])
    st_blocks = property(lambda self: self._extra[7])
    st_rdev = property(lambda self: self._extra[8])


def _stat(value):
    if isinstance(value, stat_result):
        return value
    if isinstance(value, dict):
        items = [value[name] for name in _stat_names[:7]]
        for name in _stat_names[7:]:
            ns = value.get(name + "_ns")
            items.append(int(value[name] // 1) if ns is None else ns // 1000000000)
        return stat_result(items, value)
    return stat_result(value)


def stat(path, *, dir_fd=None, follow_symlinks=True):
    """Return native metadata for an authorized path or supported descriptor."""
    if dir_fd is not None:
        raise NotImplementedError("stat: dir_fd is unsupported")
    if isinstance(path, int):
        if not follow_symlinks:
            raise ValueError("stat: cannot use fd and follow_symlinks together")
        return fstat(path)
    return _stat(_primitive("stat" if follow_symlinks else "lstat")(_path(path)))


def lstat(path, *, dir_fd=None):
    """Return metadata without following the final symbolic link."""
    if dir_fd is not None:
        raise NotImplementedError("lstat: dir_fd is unsupported")
    return _stat(_primitive("lstat")(_path(path)))


def fstat(fd):
    """Return metadata for a descriptor managed by the native bridge."""
    return _stat(_primitive("fstat")(_integer(fd)))


def readlink(path, *, dir_fd=None):
    """Read an authorized symbolic link without resolving its target."""
    if dir_fd is not None:
        raise NotImplementedError("readlink: dir_fd is unsupported")
    return _primitive("readlink")(_path(path))


class DirEntry:
    """A directory entry whose metadata queries retain capability checks."""

    def __init__(self, directory, name):
        """Store the entry's directory and name with empty metadata caches."""
        self.name = name
        self.path = directory + ("" if directory.endswith("/") else "/") + name
        self._stat = None
        self._lstat = None

    def __fspath__(self):
        """Return the entry's filesystem path."""
        return self.path

    def __repr__(self):
        """Return the entry's name in directory-entry form."""
        return "<DirEntry " + repr(self.name) + ">"

    def stat(self, *, follow_symlinks=True):
        """Return cached metadata, optionally following symbolic links."""
        if not follow_symlinks:
            if self._lstat is None:
                self._lstat = lstat(self.path)
            return self._lstat
        if self._stat is None:
            self._stat = stat(self.path)
        return self._stat

    def inode(self):
        """Return the inode number without following symbolic links."""
        return self.stat(follow_symlinks=False).st_ino

    def is_symlink(self):
        """Return whether the entry is a symbolic link."""
        try:
            return self.stat(follow_symlinks=False).st_mode & 61440 == 40960
        except FileNotFoundError:
            return False

    def is_dir(self, *, follow_symlinks=True):
        """Return whether the entry is a directory."""
        try:
            return self.stat(follow_symlinks=follow_symlinks).st_mode & 61440 == 16384
        except FileNotFoundError:
            return False

    def is_file(self, *, follow_symlinks=True):
        """Return whether the entry is a regular file."""
        try:
            return self.stat(follow_symlinks=follow_symlinks).st_mode & 61440 == 32768
        except FileNotFoundError:
            return False


class _ScandirIterator:
    def __init__(self, path):
        self._path = path
        self._names = iter(listdir(path))

    def __iter__(self):
        return self

    def __next__(self):
        return DirEntry(self._path, next(self._names))

    def close(self):
        self._names = iter(())

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def scandir(path=None):
    """Return a closeable directory-entry iterator over an authorized path."""
    return _ScandirIterator("." if path is None else _path(path))


def access(path, mode, *, dir_fd=None, effective_ids=False, follow_symlinks=True):
    """Check existence; permission probes require a native access primitive."""
    mode = _integer(mode)
    if dir_fd is not None or effective_ids:
        raise NotImplementedError("access: descriptor and effective-ID checks are unsupported")
    if mode != F_OK:
        return _primitive("access")(_path(path), mode, follow_symlinks=follow_symlinks)
    try:
        stat(path, follow_symlinks=follow_symlinks)
    except OSError:
        return False
    return True


def open(path, flags, mode=511, *, dir_fd=None):
    """Open a native sandbox descriptor; never substitute a host descriptor."""
    if dir_fd is not None:
        raise NotImplementedError("open: dir_fd is unsupported")
    return _primitive("open")(_path(path), _integer(flags), _integer(mode))


def read(fd, length, /):
    """Read bytes from a descriptor managed by the native bridge."""
    return _primitive("read")(_integer(fd), _integer(length))


def write(fd, data, /):
    """Write bytes to a descriptor managed by the native bridge."""
    return _primitive("write")(_integer(fd), data)


def close(fd):
    """Close a descriptor managed by the native bridge."""
    return _primitive("close")(_integer(fd))


def lseek(fd, position, how, /):
    """Seek a descriptor managed by the native bridge."""
    return _primitive("lseek")(_integer(fd), _integer(position), _integer(how))


def ftruncate(fd, length, /):
    """Truncate a descriptor only through the native mutation capability."""
    return _primitive("ftruncate")(_integer(fd), _integer(length))


def isatty(fd, /):
    """Query terminal status without consulting ambient host descriptors."""
    return _primitive("isatty")(_integer(fd))


def set_inheritable(fd, inheritable, /):
    """Set inheritance only for a descriptor managed by the native bridge."""
    return _primitive("set_inheritable")(_integer(fd), bool(inheritable))


def get_inheritable(fd, /):
    """Query inheritance only for a descriptor managed by the native bridge."""
    return _primitive("get_inheritable")(_integer(fd))


def mkdir(path, mode=511, *, dir_fd=None):
    """Create a directory only through the native mutation capability."""
    if dir_fd is not None:
        raise NotImplementedError("mkdir: dir_fd is unsupported")
    return _primitive("mkdir")(_path(path), _integer(mode))


def rmdir(path, *, dir_fd=None):
    """Remove a directory only through the native mutation capability."""
    if dir_fd is not None:
        raise NotImplementedError("rmdir: dir_fd is unsupported")
    return _primitive("rmdir")(_path(path))


def unlink(path, *, dir_fd=None):
    """Remove a file only through the native mutation capability."""
    if dir_fd is not None:
        raise NotImplementedError("unlink: dir_fd is unsupported")
    return _primitive("unlink")(_path(path))


remove = unlink


def rename(src, dst, *, src_dir_fd=None, dst_dir_fd=None):
    """Rename a path only through the native mutation capability."""
    if src_dir_fd is not None or dst_dir_fd is not None:
        raise NotImplementedError("rename: dir_fd is unsupported")
    return _primitive("rename")(_path(src), _path(dst))


def replace(src, dst, *, src_dir_fd=None, dst_dir_fd=None):
    """Replace a path only through the native mutation capability."""
    if src_dir_fd is not None or dst_dir_fd is not None:
        raise NotImplementedError("replace: dir_fd is unsupported")
    return _primitive("replace")(_path(src), _path(dst))


def chdir(path):
    """Change directory only if the native runtime manages working directories."""
    return _primitive("chdir")(_path(path))


def urandom(size):
    """Request cryptographic randomness only from a native authorized provider."""
    return _primitive("urandom")(_integer(size))


def _envtext(value):
    if not isinstance(value, (str, bytes)):
        value = fspath(value)
    value = fsdecode(value)
    if "\x00" in value:
        raise ValueError("embedded null byte")
    return value


def putenv(name, value, /):
    """Set the sandbox process environment, not os.environ's separate snapshot."""
    name = _envtext(name)
    value = _envtext(value)
    if "=" in name:
        raise ValueError("illegal environment variable name")
    if not name:
        raise OSError(22, "Invalid argument")
    _environment[name] = value


def unsetenv(name, /):
    """Remove a variable from the sandbox process environment."""
    name = _envtext(name)
    if not name or "=" in name:
        raise OSError(22, "Invalid argument")
    _environment.pop(name, None)


def _getenv(name):
    return _environment.get(name)


_errors = {
    0: "Success",
    1: "Operation not permitted",
    2: "No such file or directory",
    3: "No such process",
    4: "Interrupted system call",
    5: "Input/output error",
    6: "No such device or address",
    7: "Argument list too long",
    8: "Exec format error",
    9: "Bad file descriptor",
    10: "No child processes",
    11: "Resource temporarily unavailable",
    12: "Cannot allocate memory",
    13: "Permission denied",
    14: "Bad address",
    15: "Block device required",
    16: "Device or resource busy",
    17: "File exists",
    18: "Invalid cross-device link",
    19: "No such device",
    20: "Not a directory",
    21: "Is a directory",
    22: "Invalid argument",
    23: "Too many open files in system",
    24: "Too many open files",
    25: "Inappropriate ioctl for device",
    26: "Text file busy",
    27: "File too large",
    28: "No space left on device",
    29: "Illegal seek",
    30: "Read-only file system",
    31: "Too many links",
    32: "Broken pipe",
    33: "Numerical argument out of domain",
    34: "Numerical result out of range",
    35: "Resource deadlock avoided",
    36: "File name too long",
    37: "No locks available",
    38: "Function not implemented",
    39: "Directory not empty",
    40: "Too many levels of symbolic links",
    42: "No message of desired type",
    43: "Identifier removed",
    44: "Channel number out of range",
    45: "Level 2 not synchronized",
    46: "Level 3 halted",
    47: "Level 3 reset",
    48: "Link number out of range",
    49: "Protocol driver not attached",
    50: "No CSI structure available",
    51: "Level 2 halted",
    52: "Invalid exchange",
    53: "Invalid request descriptor",
    54: "Exchange full",
    55: "No anode",
    56: "Invalid request code",
    57: "Invalid slot",
    59: "Bad font file format",
    60: "Device not a stream",
    61: "No data available",
    62: "Timer expired",
    63: "Out of streams resources",
    64: "Machine is not on the network",
    65: "Package not installed",
    66: "Object is remote",
    67: "Link has been severed",
    68: "Advertise error",
    69: "Srmount error",
    70: "Communication error on send",
    71: "Protocol error",
    72: "Multihop attempted",
    73: "RFS specific error",
    74: "Bad message",
    75: "Value too large for defined data type",
    76: "Name not unique on network",
    77: "File descriptor in bad state",
    78: "Remote address changed",
    79: "Can not access a needed shared library",
    80: "Accessing a corrupted shared library",
    81: ".lib section in a.out corrupted",
    82: "Attempting to link in too many shared libraries",
    83: "Cannot exec a shared library directly",
    84: "Invalid or incomplete multibyte or wide character",
    85: "Interrupted system call should be restarted",
    86: "Streams pipe error",
    87: "Too many users",
    88: "Socket operation on non-socket",
    89: "Destination address required",
    90: "Message too long",
    91: "Protocol wrong type for socket",
    92: "Protocol not available",
    93: "Protocol not supported",
    94: "Socket type not supported",
    95: "Operation not supported",
    96: "Protocol family not supported",
    97: "Address family not supported by protocol",
    98: "Address already in use",
    99: "Cannot assign requested address",
    100: "Network is down",
    101: "Network is unreachable",
    102: "Network dropped connection on reset",
    103: "Software caused connection abort",
    104: "Connection reset by peer",
    105: "No buffer space available",
    106: "Transport endpoint is already connected",
    107: "Transport endpoint is not connected",
    108: "Cannot send after transport endpoint shutdown",
    109: "Too many references: cannot splice",
    110: "Connection timed out",
    111: "Connection refused",
    112: "Host is down",
    113: "No route to host",
    114: "Operation already in progress",
    115: "Operation now in progress",
    116: "Stale file handle",
    117: "Structure needs cleaning",
    118: "Not a XENIX named type file",
    119: "No XENIX semaphores available",
    120: "Is a named type file",
    121: "Remote I/O error",
    122: "Disk quota exceeded",
    123: "No medium found",
    124: "Wrong medium type",
    125: "Operation canceled",
    126: "Required key not available",
    127: "Key has expired",
    128: "Key has been revoked",
    129: "Key was rejected by service",
    130: "Owner died",
    131: "State not recoverable",
    132: "Operation not possible due to RF-kill",
    133: "Memory page has hardware error",
}


def strerror(code, /):
    """Return deterministic C-locale messages for portable POSIX error numbers."""
    code = _integer(code)
    if code < -2147483648 or code > 2147483647:
        raise OverflowError("Python int too large to convert to C int")
    return _errors.get(code, "Unknown error " + str(code))
