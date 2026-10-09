"""Provide daemon threads and synchronization using native tasks.

Thread targets, positional and keyword arguments, start, join, and is_alive are
supported. Native tasks cannot keep the process alive, so start requires a
truthy daemon flag. Uncaught target exceptions are unsupported and become
NotImplementedError on join; targets must handle their own exceptions.

Lock is non-reentrant and supports blocking and nonblocking acquisition.
Condition requires an explicit Lock because native tasks expose no identity
with which to implement RLock. Event and Condition support indefinite waits
and nonpositive polling timeouts; positive timeouts are unsupported. Lock
supports only its default timeout and zero. Thread identity, self-join
validation, exception hooks, and automatic shutdown joins are unavailable.
Do not join the current thread or create cyclic waits.

All synchronization requires the spawn capability. See docs/spec.md section
11.9. No host threading module is imported.
"""

import tasks as _tasks

__all__ = ["Thread", "Lock", "RLock", "Condition", "Event"]


class Lock:
    """Provide a non-reentrant mutex that may be released by any thread."""

    def __init__(self):
        self._guard = _tasks.lock()
        self._locked = False
        self._waiters = []

    def acquire(self, blocking=True, timeout=-1):
        """Acquire the lock, returning whether acquisition succeeded."""
        if not blocking and timeout != -1:
            raise ValueError("can't specify a timeout for a non-blocking call")
        if timeout < 0 and timeout != -1:
            raise ValueError("timeout value must be positive")
        if timeout != -1 and timeout != 0:
            raise NotImplementedError("timed lock acquisition is not supported")
        with self._guard:
            if not self._locked:
                self._locked = True
                return True
            if not blocking or timeout == 0:
                return False
            waiter = _tasks.channel(1)
            self._waiters.append(waiter)
        waiter.recv()
        return True

    def release(self):
        """Release the lock, handing it directly to a blocked acquirer."""
        with self._guard:
            if not self._locked:
                raise RuntimeError("release unlocked lock")
            if self._waiters:
                self._waiters.pop(0).send(True)
            else:
                self._locked = False

    def locked(self):
        """Return whether the lock is held."""
        with self._guard:
            return self._locked

    def __enter__(self):
        return self.acquire()

    def __exit__(self, exc_type, exc_value, traceback):
        self.release()
        return False


def RLock():
    """Reject reentrant locks because native tasks expose no task identity."""
    raise NotImplementedError("RLock requires native task identity")


class Condition:
    """Provide condition waiting and notification with an explicit Lock.

    Args:
        lock: A Lock shared by callers of wait and notify. The usual default
            RLock and custom lock implementations are unsupported.
    """

    def __init__(self, lock=None):
        if lock is None:
            raise NotImplementedError("Condition requires an explicit Lock")
        if not isinstance(lock, Lock):
            raise NotImplementedError("Condition supports only threading.Lock")
        self._lock = lock
        self._waiters = []

    def acquire(self, blocking=True, timeout=-1):
        """Acquire the underlying lock."""
        return self._lock.acquire(blocking, timeout)

    def release(self):
        """Release the underlying lock."""
        self._lock.release()

    def wait(self, timeout=None):
        """Release the lock until notified, then reacquire it before returning."""
        if not self._lock.locked():
            raise RuntimeError("cannot wait on un-acquired lock")
        if timeout is not None and timeout > 0:
            raise NotImplementedError("timed condition waits are not supported")
        if timeout is not None:
            self.release()
            self.acquire()
            return False
        waiter = _tasks.channel(1)
        self._waiters.append(waiter)
        self.release()
        try:
            waiter.recv()
            return True
        finally:
            self.acquire()
            if waiter in self._waiters:
                self._waiters.remove(waiter)

    def wait_for(self, predicate, timeout=None):
        """Wait until predicate is truthy and return its last result."""
        if timeout is not None and timeout > 0:
            raise NotImplementedError("timed condition waits are not supported")
        result = predicate()
        while not result:
            if timeout is not None:
                break
            self.wait()
            result = predicate()
        return result

    def notify(self, n=1):
        """Wake at most n waiting threads while retaining the lock."""
        if not self._lock.locked():
            raise RuntimeError("cannot notify on un-acquired lock")
        while self._waiters and n > 0:
            self._waiters.pop(0).send(True)
            n -= 1

    def notify_all(self):
        """Wake all waiting threads while retaining the lock."""
        self.notify(len(self._waiters))

    def __enter__(self):
        return self.acquire()

    def __exit__(self, exc_type, exc_value, traceback):
        self.release()
        return False


class Event:
    """Provide a flag that wakes all current waiters when set."""

    def __init__(self):
        self._condition = Condition(Lock())
        self._flag = False

    def is_set(self):
        """Return whether the flag is set."""
        with self._condition:
            return self._flag

    def set(self):
        """Set the flag and wake every waiter."""
        with self._condition:
            self._flag = True
            self._condition.notify_all()

    def clear(self):
        """Reset the flag without cancelling already delivered wakeups."""
        with self._condition:
            self._flag = False

    def wait(self, timeout=None):
        """Return True when set or notified, or False for an unset poll."""
        if timeout is not None and timeout > 0:
            raise NotImplementedError("timed event waits are not supported")
        with self._condition:
            if self._flag:
                return True
            return self._condition.wait(timeout)


class Thread:
    """Run a callable in a native daemon task.

    Args:
        group: Must be None, as in CPython.
        target: Callable invoked by run, or None.
        name: Thread names are unsupported; must be None.
        args: Positional arguments for target.
        kwargs: Keyword arguments for target, or None.
        daemon: Must be truthy before start; non-daemon shutdown is unavailable.
    """

    def __init__(self, group=None, target=None, name=None, args=(), kwargs=None,
                 *, daemon=None):
        if group is not None:
            raise AssertionError("group argument must be None for now")
        if name is not None:
            raise NotImplementedError("thread names are not supported")
        self._target = target
        self._args = args
        self._kwargs = {} if kwargs is None else kwargs
        self._daemon = False if daemon is None else daemon
        self._task = None
        self._guard = _tasks.lock()

    @property
    def daemon(self):
        """Return whether this thread is marked as a daemon."""
        return self._daemon

    @daemon.setter
    def daemon(self, value):
        with self._guard:
            if self._task is not None:
                raise RuntimeError("cannot set daemon status of active thread")
            self._daemon = value

    def run(self):
        """Invoke the target with its supplied arguments."""
        if self._target is not None:
            self._target(*self._args, **self._kwargs)

    def _bootstrap(self):
        try:
            self.run()
        except SystemExit:
            pass
        except BaseException:
            raise NotImplementedError("uncaught thread exceptions are not supported") from None
        finally:
            self._target = None
            self._args = ()
            self._kwargs = None

    def start(self):
        """Start the thread exactly once, returning None."""
        with self._guard:
            if self._task is not None:
                raise RuntimeError("threads can only be started once")
            if not self._daemon:
                raise NotImplementedError("native tasks support only daemon threads")
            self._task = _tasks.spawn(self._bootstrap)

    def join(self, timeout=None):
        """Wait for completion, or return immediately for a nonpositive timeout."""
        with self._guard:
            task = self._task
        if task is None:
            raise RuntimeError("cannot join thread before it is started")
        if timeout is not None:
            if timeout > 0:
                raise NotImplementedError("timed thread joins are not supported")
            return None
        task.join()

    def is_alive(self):
        """Return whether the thread has started but not yet finished."""
        with self._guard:
            return self._task is not None and not self._task.done()
