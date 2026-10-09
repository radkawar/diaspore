"""Provide type aliases, SimpleNamespace, and live read-only mapping proxies."""


def _f(): pass
FunctionType = type(_f)
LambdaType = type(lambda: 0)


class _C:
    def _m(self): pass


MethodType = type(_C()._m)
BuiltinFunctionType = type(len)
ModuleType = type(_C)  # Approximation: Diaspore modules have a distinct type.


class SimpleNamespace:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)

    def __repr__(self):
        items = ", ".join(f"{k}={v!r}" for k, v in sorted(vars(self).items()))
        return f"namespace({items})"

    def __eq__(self, other):
        return isinstance(other, SimpleNamespace) and vars(self) == vars(other)


def new_class(name, bases=(), kwds=None, exec_body=None):
    ns = {}
    if exec_body is not None:
        exec_body(ns)
    return type(name, bases, ns)


class MappingProxyType:
    """Expose a mapping without exposing its mutation methods."""

    def __init__(self, mapping):
        if not isinstance(mapping, dict):
            if not hasattr(mapping, "keys") or not hasattr(mapping, "__getitem__"):
                raise TypeError("mappingproxy() argument must be a mapping")
        self._mapping = mapping

    def __getitem__(self, key):
        return self._mapping[key]

    def __iter__(self):
        return iter(self._mapping)

    def __len__(self):
        return len(self._mapping)

    def __contains__(self, key):
        return key in self._mapping

    def __repr__(self):
        return "mappingproxy(" + repr(self._mapping) + ")"

    def __str__(self):
        return str(self._mapping)

    def __eq__(self, other):
        if isinstance(other, MappingProxyType):
            other = other._mapping
        return self._mapping == other

    def __hash__(self):
        return hash(self._mapping)

    def get(self, key, default=None):
        return self._mapping.get(key, default)

    def keys(self):
        return self._mapping.keys()

    def values(self):
        return self._mapping.values()

    def items(self):
        return self._mapping.items()

    def copy(self):
        return self._mapping.copy()
