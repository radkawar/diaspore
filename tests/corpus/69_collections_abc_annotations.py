from __future__ import annotations

import operator
import weakref
from collections.abc import Callable, Collection, Iterable, Iterator, Mapping, Sequence, Sized
from types import MappingProxyType


def apply(function: Callable[[int], int], value: int) -> int:
    return function(value)


def collect(values: Iterable[int]) -> Sequence[int]:
    return list(values)


class Scale(Callable):
    def __init__(self, factor):
        self.factor = factor

    def __call__(self, value):
        return self.factor * value


scale = Scale(3)
print(apply(scale, 7), apply(lambda value: value + 2, 5))
print(collect((1, 2, 3)))
print(isinstance(scale, Callable), issubclass(Scale, Callable))
print([kind.__name__ for kind in (Callable, Collection, Iterable, Iterator, Mapping, Sequence, Sized)])

mapping = {"first": 1}
proxy = MappingProxyType(mapping)
print(str(proxy), repr(proxy), len(proxy), list(proxy))
mapping["second"] = 2
print(proxy["second"], "first" in proxy, proxy.get("missing", 3))
print(list(proxy.keys()), list(proxy.values()), list(proxy.items()))
print(proxy == mapping, proxy.copy() == mapping, proxy == MappingProxyType(mapping))
try:
    proxy["third"] = 3
except TypeError:
    print("read-only")
try:
    hash(proxy)
except TypeError:
    print("unhashable")

for operation in [
    operator.iadd, operator.isub, operator.imul, operator.itruediv,
    operator.ifloordiv, operator.imod, operator.ipow, operator.ilshift,
    operator.irshift, operator.iand, operator.ior, operator.ixor,
]:
    print(operation(7, 2))

items = [1]
print(operator.iadd(items, [2]) is items, items)
print(operator.imul(items, 2) is items, items)


class Matrix:
    def __init__(self, value):
        self.value = value

    def __imatmul__(self, other):
        self.value += other.value
        return self


matrix = Matrix(3)
print(operator.imatmul(matrix, Matrix(4)) is matrix, matrix.value)


class Fallback:
    def __iadd__(self, other):
        return NotImplemented

    def __add__(self, other):
        return other + 10


print(operator.iadd(Fallback(), 2))


def finish(value, *, extra):
    return value + extra


target = Matrix(1)
finalizer = weakref.finalize(target, finish, 3, extra=4)
info = finalizer.peek()
print(finalizer.alive, finalizer.atexit, info[0] is target, info[1] is finish)
print(info[2], info[3])
finalizer.atexit = False
print(finalizer.atexit, finalizer(), finalizer.alive, finalizer())
print(finalizer.peek(), finalizer.detach(), finalizer.atexit)

detached = weakref.finalize(target, finish, 5, extra=6)
info = detached.detach()
print(info[0] is target, info[1] is finish, info[2], info[3])
print(detached.alive, detached(), detached.peek(), detached.detach())


def failed_finalizer():
    raise ValueError("callback failed")


failed = weakref.finalize(target, failed_finalizer)
try:
    failed()
except ValueError as error:
    print(str(error), failed.alive, failed())
