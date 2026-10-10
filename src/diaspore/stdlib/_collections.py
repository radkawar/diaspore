"""Implement the collection primitives supplied by CPython's C extension."""

from _operator import index as _index
from sys import maxsize as _maxsize

_repr_running = set()


def _ssize(value):
    value = _index(value)
    if value > _maxsize or value < -_maxsize - 1:
        raise OverflowError("Python int too large to convert to C ssize_t")
    return value


def _count_elements(mapping, iterable, /):
    get = mapping.get
    for element in iterable:
        mapping[element] = get(element, 0) + 1


class deque:
    """Store a sequence with constant-time appends and pops at either end."""

    __slots__ = ("_items", "_left", "_right", "_maxlen", "_state", "__weakref__")
    __hash__ = None
    __module__ = "collections"

    def __new__(cls, *args, **kwargs):
        result = object.__new__(cls)
        result._state = 0
        result._items = {}
        result._left = 0
        result._right = 0
        result._maxlen = None
        return result

    def __init__(self, iterable=(), maxlen=None):
        if maxlen is not None:
            if not isinstance(maxlen, int):
                raise TypeError("an integer is required")
            maxlen = _ssize(maxlen)
            if maxlen < 0:
                raise ValueError("maxlen must be non-negative")
        self._maxlen = maxlen
        deque.clear(self)
        deque.extend(self, iterable)

    @property
    def maxlen(self):
        return self._maxlen

    def append(self, value, /):
        self._items[self._right] = value
        self._right += 1
        self._state += 1
        if self._maxlen is not None and self._right - self._left > self._maxlen:
            del self._items[self._left]
            self._left += 1

    def appendleft(self, value, /):
        self._left -= 1
        self._items[self._left] = value
        self._state += 1
        if self._maxlen is not None and self._right - self._left > self._maxlen:
            self._right -= 1
            del self._items[self._right]

    def pop(self):
        if self._right == self._left:
            raise IndexError("pop from an empty deque")
        self._right -= 1
        self._state += 1
        return self._items.pop(self._right)

    def popleft(self):
        if self._right == self._left:
            raise IndexError("pop from an empty deque")
        position = self._left
        self._left += 1
        self._state += 1
        return self._items.pop(position)

    def extend(self, iterable, /):
        if iterable is self:
            iterable = list(self)
        if self._maxlen == 0:
            for _value in iterable:
                pass
        else:
            for value in iterable:
                deque.append(self, value)

    def extendleft(self, iterable, /):
        if iterable is self:
            iterable = list(self)
        if self._maxlen == 0:
            for _value in iterable:
                pass
        else:
            for value in iterable:
                deque.appendleft(self, value)

    def clear(self):
        if self._right != self._left:
            self._state += 1
            self._left = 0
            self._right = 0
            self._items = {}

    def rotate(self, n=1, /):
        n = _ssize(n)
        size = self._right - self._left
        if size <= 1:
            return
        n %= size
        if n > size // 2:
            n -= size
        self._state += 1
        while n > 0:
            self._right -= 1
            self._left -= 1
            self._items[self._left] = self._items.pop(self._right)
            n -= 1
        while n < 0:
            self._items[self._right] = self._items.pop(self._left)
            self._left += 1
            self._right += 1
            n += 1

    def reverse(self):
        left = self._left
        right = self._right - 1
        while left < right:
            self._items[left], self._items[right] = self._items[right], self._items[left]
            left += 1
            right -= 1

    def count(self, value, /):
        state = self._state
        result = 0
        for position in range(self._left, self._right):
            item = self._items[position]
            if item is value or item == value:
                result += 1
            if self._state != state:
                raise RuntimeError("deque mutated during iteration")
        return result

    def index(self, value, start=0, stop=_maxsize, /):
        start = _index(start)
        stop = _index(stop)
        size = self._right - self._left
        if start < 0:
            start = max(0, size + start)
        if stop < 0:
            stop = max(0, size + stop)
        stop = min(stop, size)
        state = self._state
        for offset in range(start, stop):
            item = self._items[self._left + offset]
            if item is value or item == value:
                return offset
            if self._state != state:
                raise RuntimeError("deque mutated during iteration")
        raise ValueError(repr(value) + " is not in deque")

    def remove(self, value, /):
        state = self._state
        for position in range(self._left, self._right):
            item = self._items[position]
            equal = item is value or bool(item == value)
            if self._state != state:
                raise IndexError("deque mutated during iteration")
            if equal:
                deque.__delitem__(self, position - self._left)
                return
        raise ValueError(repr(value) + " is not in deque")

    def insert(self, index, value, /):
        index = _ssize(index)
        size = self._right - self._left
        if self._maxlen is not None and size == self._maxlen:
            raise IndexError("deque already at its maximum size")
        if index >= size:
            deque.append(self, value)
        elif index <= -size or index == 0:
            deque.appendleft(self, value)
        else:
            deque.rotate(self, -index)
            if index < 0:
                deque.append(self, value)
            else:
                deque.appendleft(self, value)
            deque.rotate(self, index)

    def copy(self):
        if self._maxlen is None:
            result = type(self)(self)
        else:
            result = type(self)(self, self._maxlen)
        if not isinstance(result, deque):
            raise TypeError(
                type(self).__name__ + "() must return a deque, not " + type(result).__name__
            )
        return result

    __copy__ = copy

    def __len__(self):
        return self._right - self._left

    def __iter__(self):
        return _deque_iterator(self)

    def __reversed__(self):
        return _deque_reverse_iterator(self)

    def _position(self, index):
        original = index
        index = _index(index)
        if index > _maxsize or index < -_maxsize - 1:
            raise IndexError(
                "cannot fit '" + type(original).__name__ + "' into an index-sized integer"
            )
        size = self._right - self._left
        if index < 0:
            index += size
        if index < 0 or index >= size:
            raise IndexError("deque index out of range")
        return self._left + index

    def __getitem__(self, index):
        return self._items[deque._position(self, index)]

    def __setitem__(self, index, value):
        self._items[deque._position(self, index)] = value

    def __delitem__(self, index):
        offset = deque._position(self, index) - self._left
        deque.rotate(self, -offset)
        deque.popleft(self)
        deque.rotate(self, offset)

    def __contains__(self, value):
        state = self._state
        for position in range(self._left, self._right):
            item = self._items[position]
            if item is value or item == value:
                return True
            if self._state != state:
                raise RuntimeError("deque mutated during iteration")
        return False

    def __repr__(self):
        identity = id(self)
        if identity in _repr_running:
            return "[...]"
        _repr_running.add(identity)
        try:
            result = type(self).__name__ + "(" + repr(list(self))
            if self._maxlen is not None:
                result += ", maxlen=" + repr(self._maxlen)
            return result + ")"
        finally:
            _repr_running.remove(identity)

    def _compare(self, other, operation):
        if not isinstance(other, deque):
            return NotImplemented
        if operation == "eq" or operation == "ne":
            if self is other:
                return operation == "eq"
            if deque.__len__(self) != deque.__len__(other):
                return operation == "ne"
        left = iter(self)
        right = iter(other)
        missing = object()
        while True:
            a = next(left, missing)
            b = next(right, missing)
            if a is missing or b is missing:
                if operation == "eq":
                    return a is b
                if operation == "ne":
                    return a is not b
                if operation == "lt":
                    return b is not missing
                if operation == "le":
                    return a is missing
                if operation == "gt":
                    return a is not missing
                return b is missing
            if a is b or a == b:
                continue
            if operation == "eq":
                return bool(a == b)
            if operation == "ne":
                return bool(a != b)
            if operation == "lt":
                return bool(a < b)
            if operation == "le":
                return bool(a <= b)
            if operation == "gt":
                return bool(a > b)
            return bool(a >= b)

    def __eq__(self, other):
        return deque._compare(self, other, "eq")

    def __ne__(self, other):
        return deque._compare(self, other, "ne")

    def __lt__(self, other):
        return deque._compare(self, other, "lt")

    def __le__(self, other):
        return deque._compare(self, other, "le")

    def __gt__(self, other):
        return deque._compare(self, other, "gt")

    def __ge__(self, other):
        return deque._compare(self, other, "ge")

    def __add__(self, other):
        if not isinstance(other, deque):
            raise TypeError(
                'can only concatenate deque (not "' + type(other).__name__ + '") to deque'
            )
        result = deque.copy(self)
        deque.extend(result, other)
        return result

    def __iadd__(self, other):
        deque.extend(self, other)
        return self

    def __imul__(self, n):
        n = _ssize(n)
        size = self._right - self._left
        if size == 0 or n == 1:
            return self
        if n <= 0:
            deque.clear(self)
            return self
        if size == 1:
            if self._maxlen is not None:
                n = min(n, self._maxlen)
            value = self._items[self._left]
            self._state += 1
            for _unused in range(n - 1):
                self._items[self._right] = value
                self._right += 1
            return self
        if size > _maxsize // n:
            raise MemoryError()
        if self._maxlen is not None and n * size > self._maxlen:
            n = (self._maxlen + size - 1) // size
        if n > 1:
            items = list(self)
            for _unused in range(n - 1):
                deque.extend(self, items)
        return self

    def __mul__(self, n):
        n = _ssize(n)
        result = deque.copy(self)
        return deque.__imul__(result, n)

    __rmul__ = __mul__

    def __reduce__(self):
        args = () if self._maxlen is None else ((), self._maxlen)
        return type(self), args, getattr(self, "__dict__", None), iter(self)

    @classmethod
    def __class_getitem__(cls, item):
        from types import GenericAlias

        return GenericAlias(cls, item)


class _deque_iterator:
    __slots__ = ("_deque", "_state", "_position", "_remaining")
    __module__ = "collections"

    def __init__(self, iterable, index=0, /):
        if not isinstance(iterable, deque):
            raise TypeError("argument 1 must be collections.deque, not " + type(iterable).__name__)
        index = max(0, min(_ssize(index), deque.__len__(iterable)))
        self._deque = iterable
        self._state = iterable._state
        self._position = iterable._left + index
        self._remaining = deque.__len__(iterable) - index

    def __iter__(self):
        return self

    def __next__(self):
        if self._state != self._deque._state:
            self._remaining = 0
            raise RuntimeError("deque mutated during iteration")
        if self._remaining == 0:
            raise StopIteration
        value = self._deque._items[self._position]
        self._position += 1
        self._remaining -= 1
        return value

    def __length_hint__(self):
        return self._remaining

    def __reduce__(self):
        return type(self), (self._deque, deque.__len__(self._deque) - self._remaining)


class _deque_reverse_iterator(_deque_iterator):
    __slots__ = ()
    __module__ = "collections"

    def __init__(self, iterable, index=0, /):
        _deque_iterator.__init__(self, iterable, index)
        self._position = iterable._left + self._remaining - 1

    def __next__(self):
        # CPython's reverse iterator checks exhaustion before mutation.
        if self._remaining == 0:
            raise StopIteration
        if self._state != self._deque._state:
            self._remaining = 0
            raise RuntimeError("deque mutated during iteration")
        value = self._deque._items[self._position]
        self._position -= 1
        self._remaining -= 1
        return value


class defaultdict(dict):
    """Call a factory to insert values when dictionary subscription misses."""

    __slots__ = ("_default_factory",)
    __module__ = "collections"

    def __init__(self, default_factory=None, /, *args, **kwargs):
        if default_factory is not None and not callable(default_factory):
            raise TypeError("first argument must be callable or None")
        self._default_factory = default_factory
        dict.__init__(self, *args, **kwargs)

    @property
    def default_factory(self):
        return getattr(self, "_default_factory", None)

    @default_factory.setter
    def default_factory(self, value):
        self._default_factory = value

    @default_factory.deleter
    def default_factory(self):
        self._default_factory = None

    def __missing__(self, key):
        factory = self.default_factory
        if factory is None:
            raise KeyError(key)
        value = factory()
        self[key] = value
        return value

    def copy(self):
        return type(self)(self.default_factory, self)

    __copy__ = copy

    def __repr__(self):
        contents = dict.__repr__(self)
        factory = self.default_factory
        identity = id(factory)
        if identity in _repr_running:
            factory_repr = "..."
        else:
            _repr_running.add(identity)
            try:
                factory_repr = repr(factory)
            finally:
                _repr_running.remove(identity)
        return type(self).__name__ + "(" + factory_repr + ", " + contents + ")"

    def __or__(self, other):
        if not isinstance(other, dict):
            return NotImplemented
        result = type(self)(self.default_factory, self)
        dict.update(result, other)
        return result

    def __ror__(self, other):
        if not isinstance(other, dict):
            return NotImplemented
        result = type(self)(self.default_factory, other)
        dict.update(result, self)
        return result

    def __reduce__(self):
        return type(self), (self.default_factory,), None, None, iter(self.items())

    @classmethod
    def __class_getitem__(cls, item):
        from types import GenericAlias

        return GenericAlias(cls, item)
