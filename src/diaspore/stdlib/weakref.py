"""Provide weakref-compatible helpers backed by strong references."""


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
