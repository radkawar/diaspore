"""Wait for readiness of sockets owned by the sandbox's native net registry.

Only connected sockets and listeners have selectable handles. Integers are
VM-private registry identifiers, never host file descriptors. Poll, epoll,
kqueue, arbitrary file descriptors, and unconnected socket handles are not
exposed. Native runtimes retain their network capability and platform limits.
"""

from operator import index as _index

import net as _net

__all__ = ["error", "select"]
error = OSError


def _descriptor(value):
    if not isinstance(value, int):
        try:
            fileno = value.fileno
        except AttributeError:
            raise TypeError("argument must be an int, or have a fileno() method.") from None
        value = fileno()
        if not isinstance(value, int):
            raise TypeError("fileno() returned a non-integer")
    value = _index(value)
    if value < -2147483648 or value > 2147483647:
        raise OverflowError("Python int too large to convert to C int")
    if value < 0:
        raise ValueError("file descriptor cannot be a negative integer (" + str(value) + ")")
    return value


def _timeout(value):
    if value is None:
        return None
    if not isinstance(value, float):
        try:
            value = _index(value)
        except TypeError:
            raise TypeError("timeout must be a float or None") from None
    if value != value:
        raise ValueError("Invalid value NaN (not a number)")
    if value <= -9223372036.854776 or value >= 9223372036.854776:
        raise OverflowError("timestamp out of range for platform time_t")
    if value < 0:
        raise ValueError("timeout must be non-negative")
    return float(value)


@staticmethod
def select(rlist, wlist, xlist, timeout=None, /):
    """Return ready objects from the three input iterables in their original order.

    Args:
        rlist: Socket objects or registry handles to wait for readable data.
        wlist: Socket objects or registry handles to wait for writable capacity.
        xlist: Socket objects or registry handles to wait for exceptional data.
        timeout: Nonnegative seconds to wait, or None to wait indefinitely.

    Returns:
        Three lists retaining original objects, input order, and duplicates.

    Raises:
        TypeError: A descriptor or timeout has an invalid type.
        ValueError: A descriptor or timeout is negative.
        OSError: A nonnegative handle does not identify a live registry socket.
    """
    timeout = _timeout(timeout)
    readers = list(rlist)
    read_ids = [_descriptor(value) for value in readers]
    writers = list(wlist)
    write_ids = [_descriptor(value) for value in writers]
    exceptional = list(xlist)
    error_ids = [_descriptor(value) for value in exceptional]
    ready_read, ready_write, ready_error = _net.select(read_ids, write_ids, error_ids, timeout)
    read_set, write_set, error_set = set(ready_read), set(ready_write), set(ready_error)
    return (
        [value for value, fd in zip(readers, read_ids) if fd in read_set],
        [value for value, fd in zip(writers, write_ids) if fd in write_set],
        [value for value, fd in zip(exceptional, error_ids) if fd in error_set],
    )
