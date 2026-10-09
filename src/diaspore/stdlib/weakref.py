"""Provide strong-reference helpers and explicitly invoked finalizers.

References and weak containers retain their targets. Finalizers support call,
detach, peek, alive and a stored atexit flag, but never run automatically on
collection or process exit. Programs must invoke or detach finalizers explicitly.
"""


class ref:
    def __init__(self, obj, callback=None):
        self._obj = obj
    def __call__(self):
        return self._obj


def proxy(obj, callback=None):
    return obj


class WeakValueDictionary(dict):
    pass


class WeakKeyDictionary(dict):
    pass


class WeakSet(set):
    pass


class finalize:
    """Run a registered callback at most once through explicit invocation."""

    def __init__(self, obj, func, *args, **kwargs):
        self._obj = obj
        self._func = func
        self._args = args
        self._kwargs = kwargs
        self._alive = True
        self._atexit = True

    @property
    def alive(self):
        return self._alive

    @property
    def atexit(self):
        return self._alive and self._atexit

    @atexit.setter
    def atexit(self, value):
        if self._alive:
            self._atexit = bool(value)

    def peek(self):
        if self._alive:
            return self._obj, self._func, self._args, self._kwargs
        return None

    def detach(self):
        info = self.peek()
        if self._alive:
            self._alive = False
            self._obj = None
            self._func = None
            self._args = None
            self._kwargs = None
        return info

    def __call__(self):
        info = self.detach()
        if info is not None:
            _, func, args, kwargs = info
            return func(*args, **kwargs)
        return None
