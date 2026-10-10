"""Provide reference and proxy protocols without pretending to collect targets.

The portable runtime retains both targets and reference registrations. Callbacks
are stored but never invoked automatically. getweakrefs() and getweakrefcount()
report these retained registrations, including references dropped by callers.
"""

from _thread import allocate_lock

_registry = {}
_registry_lock = allocate_lock()
_UNSET = object()
_NONWEAK = (
    type(None),
    bool,
    int,
    float,
    complex,
    str,
    bytes,
    bytearray,
    list,
    dict,
    tuple,
    range,
    object,
)


def _check_target(obj):
    cls = type(obj)
    offset = getattr(cls, "__weakrefoffset__", None)
    if offset is not None:
        allowed = bool(offset)
    elif cls in _NONWEAK or isinstance(obj, (int, tuple, bytes)):
        allowed = False
    elif cls in (ReferenceType, ProxyType, CallableProxyType):
        allowed = False
    else:
        allowed = False
        for base in cls.__mro__:
            if base in _NONWEAK:
                continue
            namespace = base.__dict__
            if "__slots__" not in namespace:
                allowed = True
                break
            slots = namespace["__slots__"]
            if isinstance(slots, str):
                slots = (slots,)
            if "__weakref__" in slots:
                allowed = True
                break
    if not allowed:
        raise TypeError("cannot create weak reference to '" + cls.__name__ + "' object")


def _target(reference):
    return object.__getattribute__(reference, "_obj")


def _unwrap(value):
    if type(value) in (ProxyType, CallableProxyType):
        return _target(value)
    return value


class ReferenceType:
    """Retain a target while exposing the weak-reference call and hash protocol."""

    __slots__ = ("_obj", "_callback", "_hash")

    def __new__(cls, obj, callback=None, /, **kwargs):
        if not issubclass(cls, ReferenceType):
            raise TypeError("ref.__new__() requires a ReferenceType subtype")
        if cls is ReferenceType and kwargs:
            raise TypeError("ref() takes no keyword arguments")
        _check_target(obj)
        with _registry_lock:
            references = _registry.get(id(obj))
            if references is None:
                references = []
                _registry[id(obj)] = references
            if cls is ReferenceType and callback is None:
                for reference in references:
                    if type(reference) is ReferenceType and reference.__callback__ is None:
                        return reference
            reference = object.__new__(cls)
            object.__setattr__(reference, "_obj", obj)
            object.__setattr__(reference, "_callback", callback)
            object.__setattr__(reference, "_hash", _UNSET)
            if cls is ReferenceType and callback is None:
                references.insert(0, reference)
            else:
                index = 0
                while index < len(references):
                    item = references[index]
                    if type(item) is ReferenceType and item.__callback__ is None:
                        index += 1
                    elif (
                        type(item) in (ProxyType, CallableProxyType)
                        and object.__getattribute__(item, "_callback") is None
                    ):
                        index += 1
                    else:
                        break
                references.insert(index, reference)
            return reference

    def __init__(self, obj, callback=None, /, **kwargs):
        # __new__ owns initialization, including subclass calls from WeakMethod.
        if type(self) is ReferenceType and kwargs:
            raise TypeError("ref() takes no keyword arguments")

    @property
    def __callback__(self):
        """Return the stored callback without scheduling collection callbacks."""
        return object.__getattribute__(self, "_callback")

    def __call__(self):
        return _target(self)

    def __hash__(self):
        value = object.__getattribute__(self, "_hash")
        if value is _UNSET:
            value = hash(_target(self))
            object.__setattr__(self, "_hash", value)
        return value

    def __eq__(self, other):
        if not isinstance(other, ReferenceType):
            return NotImplemented
        return _target(self) == _target(other)

    def __ne__(self, other):
        if not isinstance(other, ReferenceType):
            return NotImplemented
        return _target(self) != _target(other)

    def __repr__(self):
        obj = _target(self)
        return (
            "<weakref at "
            + hex(id(self))
            + "; to '"
            + type(obj).__name__
            + "' at "
            + hex(id(obj))
            + ">"
        )


ref = ReferenceType


class _ProxyBase:
    __hash__ = None

    def __new__(cls, *args, **kwargs):
        raise TypeError("cannot create '" + cls.__name__ + "' instances")

    def __getattribute__(self, name):
        return getattr(_target(self), name)

    def __setattr__(self, name, value):
        setattr(_target(self), name, value)

    def __delattr__(self, name):
        delattr(_target(self), name)

    def __repr__(self):
        obj = _target(self)
        return (
            "<weakproxy at "
            + hex(id(self))
            + " to "
            + type(obj).__name__
            + " at "
            + hex(id(obj))
            + ">"
        )

    def __str__(self):
        return str(_target(self))

    def __bytes__(self):
        return _target(self).__bytes__()

    def __bool__(self):
        return bool(_target(self))

    def __len__(self):
        return len(_target(self))

    def __iter__(self):
        return iter(_target(self))

    def __next__(self):
        return next(_target(self))

    def __reversed__(self):
        return _target(self).__reversed__()

    def __contains__(self, item):
        return item in _target(self)

    def __getitem__(self, key):
        return _target(self)[_unwrap(key)]

    def __setitem__(self, key, value):
        _target(self)[key] = value

    def __delitem__(self, key):
        del _target(self)[key]

    def __eq__(self, other):
        return _target(self) == _unwrap(other)

    def __ne__(self, other):
        return _target(self) != _unwrap(other)

    def __lt__(self, other):
        return _target(self) < _unwrap(other)

    def __le__(self, other):
        return _target(self) <= _unwrap(other)

    def __gt__(self, other):
        return _target(self) > _unwrap(other)

    def __ge__(self, other):
        return _target(self) >= _unwrap(other)

    def __int__(self):
        return int(_target(self))

    def __float__(self):
        return float(_target(self))

    def __index__(self):
        from _operator import index

        return index(_target(self))

    def __neg__(self):
        return -_target(self)

    def __pos__(self):
        return +_target(self)

    def __abs__(self):
        return abs(_target(self))

    def __invert__(self):
        return ~_target(self)

    def __add__(self, other):
        return _target(self) + _unwrap(other)

    def __radd__(self, other):
        return _unwrap(other) + _target(self)

    def __sub__(self, other):
        return _target(self) - _unwrap(other)

    def __rsub__(self, other):
        return _unwrap(other) - _target(self)

    def __mul__(self, other):
        return _target(self) * _unwrap(other)

    def __rmul__(self, other):
        return _unwrap(other) * _target(self)

    def __truediv__(self, other):
        return _target(self) / _unwrap(other)

    def __rtruediv__(self, other):
        return _unwrap(other) / _target(self)

    def __floordiv__(self, other):
        return _target(self) // _unwrap(other)

    def __rfloordiv__(self, other):
        return _unwrap(other) // _target(self)

    def __mod__(self, other):
        return _target(self) % _unwrap(other)

    def __rmod__(self, other):
        return _unwrap(other) % _target(self)

    def __divmod__(self, other):
        return divmod(_target(self), _unwrap(other))

    def __rdivmod__(self, other):
        return divmod(_unwrap(other), _target(self))

    def __pow__(self, other, modulo=None):
        return pow(_target(self), _unwrap(other), _unwrap(modulo))

    def __rpow__(self, other):
        return pow(_unwrap(other), _target(self))

    def __lshift__(self, other):
        return _target(self) << _unwrap(other)

    def __rlshift__(self, other):
        return _unwrap(other) << _target(self)

    def __rshift__(self, other):
        return _target(self) >> _unwrap(other)

    def __rrshift__(self, other):
        return _unwrap(other) >> _target(self)

    def __and__(self, other):
        return _target(self) & _unwrap(other)

    def __rand__(self, other):
        return _unwrap(other) & _target(self)

    def __or__(self, other):
        return _target(self) | _unwrap(other)

    def __ror__(self, other):
        return _unwrap(other) | _target(self)

    def __xor__(self, other):
        return _target(self) ^ _unwrap(other)

    def __rxor__(self, other):
        return _unwrap(other) ^ _target(self)

    def __matmul__(self, other):
        return _target(self) @ _unwrap(other)

    def __rmatmul__(self, other):
        return _unwrap(other) @ _target(self)

    def __iadd__(self, other):
        obj = _target(self)
        obj += _unwrap(other)
        return obj

    def __isub__(self, other):
        obj = _target(self)
        obj -= _unwrap(other)
        return obj

    def __imul__(self, other):
        obj = _target(self)
        obj *= _unwrap(other)
        return obj

    def __itruediv__(self, other):
        obj = _target(self)
        obj /= _unwrap(other)
        return obj

    def __ifloordiv__(self, other):
        obj = _target(self)
        obj //= _unwrap(other)
        return obj

    def __imod__(self, other):
        obj = _target(self)
        obj %= _unwrap(other)
        return obj

    def __ipow__(self, other):
        obj = _target(self)
        obj **= _unwrap(other)
        return obj

    def __ilshift__(self, other):
        obj = _target(self)
        obj <<= _unwrap(other)
        return obj

    def __irshift__(self, other):
        obj = _target(self)
        obj >>= _unwrap(other)
        return obj

    def __iand__(self, other):
        obj = _target(self)
        obj &= _unwrap(other)
        return obj

    def __ior__(self, other):
        obj = _target(self)
        obj |= _unwrap(other)
        return obj

    def __ixor__(self, other):
        obj = _target(self)
        obj ^= _unwrap(other)
        return obj

    def __imatmul__(self, other):
        obj = _target(self)
        obj @= _unwrap(other)
        return obj


class ProxyType(_ProxyBase):
    """Forward operations and attribute access to a retained non-callable target."""

    def __init_subclass__(cls, **kwargs):
        raise TypeError("type 'weakref.ProxyType' is not an acceptable base type")


class CallableProxyType(_ProxyBase):
    """Forward calls as well as ordinary proxy operations to a retained target."""

    def __init_subclass__(cls, **kwargs):
        raise TypeError("type 'weakref.CallableProxyType' is not an acceptable base type")

    def __call__(self, *args, **kwargs):
        return _target(self)(*args, **kwargs)


def proxy(obj, callback=None, /):
    """Return a transparent, unhashable proxy retaining the target."""
    _check_target(obj)
    cls = CallableProxyType if callable(obj) else ProxyType
    with _registry_lock:
        references = _registry.get(id(obj))
        if references is None:
            references = []
            _registry[id(obj)] = references
        if callback is None:
            for reference in references:
                if (
                    type(reference) is cls
                    and object.__getattribute__(reference, "_callback") is None
                ):
                    return reference
        reference = object.__new__(cls)
        object.__setattr__(reference, "_obj", obj)
        object.__setattr__(reference, "_callback", callback)
        index = 0
        if (
            references
            and type(references[0]) is ReferenceType
            and references[0].__callback__ is None
        ):
            index = 1
        if callback is not None and index < len(references):
            item = references[index]
            if (
                type(item) in (ProxyType, CallableProxyType)
                and object.__getattribute__(item, "_callback") is None
            ):
                index += 1
        references.insert(index, reference)
        return reference


def getweakrefs(obj, /):
    """Return a new list containing all retained reference registrations."""
    with _registry_lock:
        return list(_registry.get(id(obj), ()))


def getweakrefcount(obj, /):
    """Return the number of retained references and proxies for the target."""
    with _registry_lock:
        return len(_registry.get(id(obj), ()))


def _remove_dead_weakref(dictionary, key, /):
    """Remove an entry only if its stored reference actually has a dead target."""
    if not isinstance(dictionary, dict):
        raise TypeError("_remove_dead_weakref() argument 1 must be dict")
    with _registry_lock:
        reference = dictionary.get(key, _UNSET)
        if reference is _UNSET:
            return
        if not isinstance(reference, ReferenceType):
            raise TypeError("not a weakref")
        # Inspect reference storage, not a subclass's potentially overridden call.
        if _target(reference) is None:
            del dictionary[key]
