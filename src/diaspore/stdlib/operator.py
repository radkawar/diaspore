"""Provide callable operators in the Diaspore subset."""


def lt(a, b): return a < b
def le(a, b): return a <= b
def eq(a, b): return a == b
def ne(a, b): return a != b
def ge(a, b): return a >= b
def gt(a, b): return a > b
def not_(a): return not a
def truth(a): return bool(a)
def is_(a, b): return a is b
def is_not(a, b): return a is not b
def abs_(a): return abs(a)
def add(a, b): return a + b
def and_(a, b): return a & b
def floordiv(a, b): return a // b
def index(a): return a.__index__() if hasattr(a, "__index__") else int(a)
def inv(a): return ~a
def invert(a): return ~a
def lshift(a, b): return a << b
def mod(a, b): return a % b
def mul(a, b): return a * b
def matmul(a, b): return a @ b
def neg(a): return -a
def or_(a, b): return a | b
def pos(a): return +a
def pow(a, b): return a ** b
def rshift(a, b): return a >> b
def sub(a, b): return a - b
def truediv(a, b): return a / b
def xor(a, b): return a ^ b
def concat(a, b): return a + b
def contains(a, b): return b in a
def countOf(a, b): return sum(1 for x in a if x == b)
def delitem(a, b): del a[b]
def getitem(a, b): return a[b]
def indexOf(a, b):
    for i, x in enumerate(a):
        if x == b:
            return i
    raise ValueError("sequence.index(x): x not in sequence")
def setitem(a, b, c): a[b] = c
def length_hint(obj, default=0):
    try:
        return len(obj)
    except TypeError:
        return default


def iadd(a, b):
    a += b
    return a


def isub(a, b):
    a -= b
    return a


def imul(a, b):
    a *= b
    return a


def imatmul(a, b):
    a @= b
    return a


def itruediv(a, b):
    a /= b
    return a


def ifloordiv(a, b):
    a //= b
    return a


def imod(a, b):
    a %= b
    return a


def ipow(a, b):
    a **= b
    return a


def ilshift(a, b):
    a <<= b
    return a


def irshift(a, b):
    a >>= b
    return a


def iand(a, b):
    a &= b
    return a


def ior(a, b):
    a |= b
    return a


def ixor(a, b):
    a ^= b
    return a


class attrgetter:
    def __init__(self, *names):
        self._names = names
    def __call__(self, obj):
        if len(self._names) == 1:
            return _resolve_attr(obj, self._names[0])
        return tuple(_resolve_attr(obj, n) for n in self._names)


def _resolve_attr(obj, name):
    for part in name.split("."):
        obj = getattr(obj, part)
    return obj


class itemgetter:
    def __init__(self, *items):
        self._items = items
    def __call__(self, obj):
        if len(self._items) == 1:
            return obj[self._items[0]]
        return tuple(obj[i] for i in self._items)


class methodcaller:
    def __init__(self, name, *args, **kwargs):
        self._name = name
        self._args = args
        self._kwargs = kwargs
    def __call__(self, obj):
        return getattr(obj, self._name)(*self._args, **self._kwargs)


__lt__ = lt
__le__ = le
__eq__ = eq
__ne__ = ne
__ge__ = ge
__gt__ = gt
__add__ = add
__sub__ = sub
__mul__ = mul
__matmul__ = matmul
__truediv__ = truediv
__floordiv__ = floordiv
__mod__ = mod
__pow__ = pow
__lshift__ = lshift
__rshift__ = rshift
__and__ = and_
__or__ = or_
__xor__ = xor
__neg__ = neg
__pos__ = pos
__invert__ = invert
__abs__ = abs_
__getitem__ = getitem
__setitem__ = setitem
__delitem__ = delitem
__contains__ = contains
__iadd__ = iadd
__isub__ = isub
__imul__ = imul
__imatmul__ = imatmul
__itruediv__ = itruediv
__ifloordiv__ = ifloordiv
__imod__ = imod
__ipow__ = ipow
__ilshift__ = ilshift
__irshift__ = irshift
__iand__ = iand
__ior__ = ior
__ixor__ = ixor
