"""Capability-checked host primitives used by portable POSIX providers."""

import errno
import os
import weakref


def _close_descriptors(descriptors):
    for descriptor in tuple(descriptors.values()):
        try:
            os.close(descriptor)
        except OSError:
            pass
    descriptors.clear()


def filesystem_primitives(interp):
    """Return operations over private VM descriptors, never ambient host FDs."""
    descriptors = {}
    next_descriptor = 3
    interp.close_files = weakref.finalize(interp, _close_descriptors, descriptors)

    def checked(fd):
        try:
            return descriptors[fd]
        except (KeyError, TypeError):
            raise OSError(errno.EBADF, "Bad file descriptor") from None

    def stat_values(value):
        return {
            name: getattr(value, name)
            for name in (
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
                "st_atime_ns",
                "st_mtime_ns",
                "st_ctime_ns",
                "st_blksize",
                "st_blocks",
                "st_rdev",
            )
            if hasattr(value, name)
        }

    def stat(path):
        return stat_values(os.stat(interp.require_fs("read", path)))

    def lstat(path):
        interp.require_fs("read", path)
        parent = interp.require_fs("read", os.path.dirname(os.path.abspath(path)))
        return stat_values(os.lstat(os.path.join(parent, os.path.basename(path))))

    def readlink(path):
        interp.require_fs("read", path)
        parent = interp.require_fs("read", os.path.dirname(os.path.abspath(path)))
        return os.readlink(os.path.join(parent, os.path.basename(path)))

    def open_file(path, flags, mode=0o777):
        nonlocal next_descriptor
        writing = flags & os.O_ACCMODE in (os.O_WRONLY, os.O_RDWR) or flags & (
            os.O_CREAT | os.O_TRUNC | os.O_APPEND
        )
        resolved = interp.require_fs("write" if writing else "read", path)
        if flags & os.O_ACCMODE == os.O_RDWR:
            interp.require_fs("read", path)
        host = os.open(resolved, flags, mode)
        os.set_inheritable(host, False)
        descriptor = next_descriptor
        next_descriptor += 1
        descriptors[descriptor] = host
        return descriptor

    def close(fd):
        host = checked(fd)
        os.close(host)
        del descriptors[fd]

    def set_inheritable(fd, value):
        host = checked(fd)
        if value:
            raise PermissionError("sandbox descriptors cannot be inherited")
        os.set_inheritable(host, False)

    def urandom(size):
        if interp.deterministic:
            raise PermissionError("system entropy is unavailable in deterministic mode")
        if interp.durable:
            return bytes.fromhex(interp.effect("_os", "urandom", lambda: os.urandom(size).hex()))
        return os.urandom(size)

    return {
        "stat": stat,
        "lstat": lstat,
        "readlink": readlink,
        "open": open_file,
        "mkdir": lambda path, mode=0o777: os.mkdir(interp.require_fs("write", path), mode),
        "read": lambda fd, size: os.read(checked(fd), size),
        "write": lambda fd, data: os.write(checked(fd), interp._raw(data)),
        "lseek": lambda fd, offset, whence: os.lseek(checked(fd), offset, whence),
        "ftruncate": lambda fd, length: os.ftruncate(checked(fd), length),
        "close": close,
        "fstat": lambda fd: stat_values(os.fstat(checked(fd))),
        "isatty": lambda fd: os.isatty(checked(fd)),
        "set_inheritable": set_inheritable,
        "get_inheritable": lambda fd: os.get_inheritable(checked(fd)),
        "urandom": urandom,
    }
