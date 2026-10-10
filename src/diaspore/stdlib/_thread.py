"""Provide low-level locks and thread entry points over native logical tasks.

Runtimes without tasks support uncontended locks and reentrant single-thread
locking. Starting a thread or waiting indefinitely for a held lock requires
native task support. Native tasks require the spawn capability; locks do not.
"""

TIMEOUT_MAX = 9223372036.0
error = RuntimeError
_UNSET = object()
_backend = _UNSET
_running = {}
_sentinels = {}


def _tasks():
    global _backend
    if _backend is _UNSET:
        try:
            import tasks
        except ImportError:
            _backend = None
        else:
            _backend = tasks
    return _backend


def _index(value):
    if isinstance(value, int):
        return value
    method = getattr(type(value), "__index__", None)
    if method is None:
        raise TypeError("'" + type(value).__name__ + "' object cannot be interpreted as an integer")
    result = method(value)
    if not isinstance(result, int):
        raise TypeError("__index__ returned non-int")
    return result


def _acquire_args(blocking, timeout):
    blocking = bool(blocking)
    if isinstance(timeout, (str, bytes)):
        raise TypeError(
            "'" + type(timeout).__name__ + "' object cannot be interpreted as a real number"
        )
    timeout = float(timeout)
    if timeout != timeout:
        raise ValueError("Invalid value NaN (not a number)")
    if timeout > TIMEOUT_MAX or timeout < -TIMEOUT_MAX:
        raise OverflowError("timestamp out of range for platform time_t")
    if not blocking and timeout != -1:
        raise ValueError("can't specify a timeout for a non-blocking call")
    if timeout < 0 and timeout != -1:
        raise ValueError("timeout value must be positive")
    return blocking, timeout


def get_ident():
    """Return the current logical thread identity, including generator calls."""
    backend = _tasks()
    if backend is None:
        return 1
    return backend.get_ident()


class LockType:
    """Provide a non-reentrant lock that may be released by another thread."""

    def __new__(cls, *args, **kwargs):
        raise TypeError("cannot create '_thread.lock' instances")

    def acquire(self, blocking=True, timeout=-1):
        """Acquire the lock, returning False for an unsuccessful timed poll."""
        blocking, timeout = _acquire_args(blocking, timeout)
        if self._native is not None:
            return self._native.acquire(blocking, timeout)
        if not self._locked:
            self._locked = True
            return True
        if not blocking or timeout == 0:
            return False
        if timeout == -1:
            raise RuntimeError("cannot wait for a held lock in a single-threaded runtime")
        import time

        time.sleep(timeout)
        return False

    def release(self):
        """Release the lock, raising RuntimeError if it was already unlocked."""
        if self._native is not None:
            self._native.release()
        elif not self._locked:
            raise RuntimeError("release unlocked lock")
        else:
            self._locked = False

    def locked(self):
        """Return whether the lock is held."""
        if self._native is not None:
            return self._native.locked()
        return self._locked

    def _at_fork_reinit(self):
        backend = _tasks()
        self._native = None if backend is None else backend.lock()
        self._locked = False

    def __enter__(self):
        return self.acquire()

    def __exit__(self, exc_type, exc_value, traceback):
        self.release()

    def __repr__(self):
        state = "locked" if self.locked() else "unlocked"
        return "<" + state + " _thread.lock object at " + hex(id(self)) + ">"

    acquire_lock = acquire
    release_lock = release
    locked_lock = locked


def allocate_lock():
    """Create an unlocked mutex without starting a task."""
    lock = object.__new__(LockType)
    lock._at_fork_reinit()
    return lock


class RLock:
    """Provide an owner-checked recursive lock with Condition support."""

    def __init__(self):
        self._lock = allocate_lock()
        self._owner = None
        self._depth = 0

    def acquire(self, blocking=True, timeout=-1):
        """Acquire the lock, incrementing the current owner's recursion count."""
        blocking, timeout = _acquire_args(blocking, timeout)
        ident = get_ident()
        if self._owner == ident:
            self._depth += 1
            return True
        if not self._lock.acquire(blocking, timeout):
            return False
        self._owner = ident
        self._depth = 1
        return True

    def release(self):
        """Release one recursion level, requiring ownership by the caller."""
        if self._depth == 0 or self._owner != get_ident():
            raise RuntimeError("cannot release un-acquired lock")
        self._depth -= 1
        if self._depth == 0:
            self._owner = None
            self._lock.release()

    def _release_save(self):
        if self._depth == 0:
            raise RuntimeError("cannot release un-acquired lock")
        state = (self._depth, self._owner)
        self._depth = 0
        self._owner = None
        self._lock.release()
        return state

    def _acquire_restore(self, state):
        if not isinstance(state, tuple) or len(state) != 2:
            raise TypeError("_acquire_restore() argument must be a 2-item tuple")
        depth = _index(state[0])
        owner = _index(state[1])
        self._lock.acquire()
        self._depth = depth
        self._owner = owner

    def _is_owned(self):
        return self._depth > 0 and self._owner == get_ident()

    def _recursion_count(self):
        return self._depth if self._owner == get_ident() else 0

    def _at_fork_reinit(self):
        self._lock._at_fork_reinit()
        self._owner = None
        self._depth = 0

    def __enter__(self):
        return self.acquire()

    def __exit__(self, exc_type, exc_value, traceback):
        self.release()

    def __repr__(self):
        state = "locked" if self._depth else "unlocked"
        owner = 0 if self._owner is None else self._owner
        return (
            "<"
            + state
            + " _thread.RLock object owner="
            + str(owner)
            + " count="
            + str(self._depth)
            + " at "
            + hex(id(self))
            + ">"
        )


def _set_sentinel():
    """Create a lock released when the current low-level thread exits."""
    lock = allocate_lock()
    _sentinels[get_ident()] = lock
    return lock


class _UnraisableArgs:
    def __init__(self, exc, function):
        self.exc_type = type(exc)
        self.exc_value = exc
        self.exc_traceback = exc.__traceback__
        self.err_msg = "Exception ignored in thread started by"
        self.object = function


def start_new_thread(function, args, kwargs=_UNSET, /):
    """Start a native task and return its logical thread identity."""
    if not callable(function):
        raise TypeError("first arg must be callable")
    if not isinstance(args, tuple):
        raise TypeError("2nd arg must be a tuple")
    if kwargs is _UNSET:
        kwargs = {}
    elif not isinstance(kwargs, dict):
        raise TypeError("optional 3rd arg must be a dictionary")
    backend = _tasks()
    if backend is None:
        raise RuntimeError("threads are unavailable in this runtime")
    started = backend.channel(1)

    def bootstrap():
        ident = get_ident()
        _running[ident] = True
        started.send(ident)
        try:
            function(*args, **kwargs)
        except SystemExit:
            pass
        except BaseException as exc:
            import sys

            hook = getattr(sys, "unraisablehook", None)
            if hook is None:
                sys.excepthook(type(exc), exc, exc.__traceback__)
            else:
                hook(_UnraisableArgs(exc, function))
        finally:
            import sys

            context_module = sys.modules.get("_contextvars")
            if context_module is not None:
                context_module._clear_thread_context(ident)
            sentinel = _sentinels.pop(ident, None)
            if sentinel is not None and sentinel.locked():
                sentinel.release()
            _running.pop(ident, None)

    backend.spawn(bootstrap)
    return started.recv()


def exit():
    """Exit the current thread by raising SystemExit."""
    raise SystemExit


def interrupt_main(signum=2, /):
    """Interrupt the main thread without interrupting the requesting worker."""
    signum = _index(signum)
    if signum <= 0 or signum >= 65:
        raise ValueError("signal number out of range")
    backend = _tasks()
    if backend is not None:
        interrupt = getattr(backend, "interrupt_main", None)
        if interrupt is not None:
            return interrupt(signum)
        main_ident = getattr(backend, "main_ident", None)
        if signum == 2 and main_ident is not None and get_ident() == main_ident():
            raise KeyboardInterrupt
        raise NotImplementedError("this runtime cannot deliver main-thread signals")
    if signum != 2:
        raise NotImplementedError("only SIGINT is supported in a single-threaded runtime")
    raise KeyboardInterrupt


def stack_size(size=_UNSET, /):
    """Return the runtime-managed default stack size; reject custom sizes."""
    if size is _UNSET:
        return 0
    size = _index(size)
    if size < 0:
        raise ValueError("size must be 0 or a positive value")
    if size == 0:
        return 0
    if size < 32768:
        raise ValueError("size not valid: " + str(size) + " bytes")
    raise RuntimeError("setting stack size is unsupported by this runtime")


def _count():
    return len(_running)


def daemon_threads_allowed():
    """Return whether this runtime can start daemon threads."""
    return _tasks() is not None


def _is_main_interpreter():
    return True


allocate = allocate_lock
start_new = start_new_thread
exit_thread = exit
