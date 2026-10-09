"""Provide type aliases and SimpleNamespace in the Diaspore subset."""


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
