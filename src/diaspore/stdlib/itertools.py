"""Implement CPython's iterator primitives using portable iterator state machines."""

from _operator import index as _index
from sys import maxsize as _maxsize

__all__ = [
    "accumulate",
    "batched",
    "chain",
    "combinations",
    "combinations_with_replacement",
    "compress",
    "count",
    "cycle",
    "dropwhile",
    "filterfalse",
    "groupby",
    "islice",
    "pairwise",
    "permutations",
    "product",
    "repeat",
    "starmap",
    "takewhile",
    "tee",
    "zip_longest",
]

_missing = object()


def _ssize(value):
    value = _index(value)
    if value > _maxsize or value < -_maxsize - 1:
        raise OverflowError("Python int too large to convert to C ssize_t")
    return value


class _Iterator:
    __slots__ = ()

    def __iter__(self):
        return self


class count(_Iterator):
    """Yield consecutive numbers, adding step after each value."""

    __slots__ = ("_value", "_step", "_fast")

    def __init__(self, start=0, step=1):
        """Initialize the iterator state from the supplied inputs."""
        for value in (start, step):
            kind = type(value)
            if not (
                isinstance(value, (int, float, complex))
                or hasattr(kind, "__index__")
                or hasattr(kind, "__int__")
                or hasattr(kind, "__float__")
            ):
                raise TypeError("a number is required")
        self._fast = False
        if isinstance(start, int) and isinstance(step, int) and _index(step) == 1:
            integer = _index(start)
            if -_maxsize - 1 <= integer <= _maxsize:
                start = integer
                self._fast = True
        self._value = start
        self._step = step

    def __next__(self):
        """Return the next value, raising StopIteration when exhausted."""
        value = self._value
        if self._fast and value < _maxsize:
            self._value = value + 1
        else:
            self._fast = False
            self._value = value + self._step
        return value

    def __repr__(self):
        """Return the iterator's constructor-style representation."""
        result = type(self).__name__ + "(" + repr(self._value)
        if not self._fast and (not isinstance(self._step, int) or _index(self._step) != 1):
            result += ", " + repr(self._step)
        return result + ")"

    def __reduce__(self):
        """Return the constructor and arguments for restoring this state."""
        if self._fast:
            return type(self), (self._value,)
        return type(self), (self._value, self._step)


class repeat(_Iterator):
    """Yield the same object a fixed number of times, or indefinitely."""

    __slots__ = ("_object", "_remaining")

    def __init__(self, object, times=_missing):
        """Initialize the iterator state from the supplied inputs."""
        self._object = object
        self._remaining = -1 if times is _missing else max(0, _ssize(times))

    def __next__(self):
        """Return the next value, raising StopIteration when exhausted."""
        if self._remaining == 0:
            raise StopIteration
        if self._remaining > 0:
            self._remaining -= 1
        return self._object

    def __length_hint__(self):
        """Return remaining repetitions, rejecting an unbounded repeat."""
        if self._remaining < 0:
            raise TypeError("len() of unsized object")
        return self._remaining

    def __repr__(self):
        """Return the iterator's constructor-style representation."""
        result = type(self).__name__ + "(" + repr(self._object)
        if self._remaining >= 0:
            result += ", " + repr(self._remaining)
        return result + ")"

    def __reduce__(self):
        """Return the constructor and arguments for restoring this state."""
        if self._remaining < 0:
            return type(self), (self._object,)
        return type(self), (self._object, self._remaining)


class chain(_Iterator):
    """Yield each input iterable in turn without opening later inputs early."""

    __slots__ = ("_source", "_active")

    def __init__(self, *iterables):
        """Initialize the iterator state from the supplied inputs."""
        self._source = iter(iterables)
        self._active = None

    @classmethod
    def from_iterable(cls, iterable, /):
        """Create a chain that lazily obtains inputs from one iterable."""
        source = iter(iterable)
        result = object.__new__(cls)
        result._source = source
        result._active = None
        return result

    def __next__(self):
        """Return the next value, raising StopIteration when exhausted."""
        while self._source is not None:
            if self._active is None:
                try:
                    self._active = iter(next(self._source))
                except BaseException:
                    self._source = None
                    raise
            try:
                return next(self._active)
            except StopIteration:
                self._active = None
        raise StopIteration


class islice(_Iterator):
    """Slice an iterator, consuming skipped values only when advanced."""

    __slots__ = ("_iterator", "_next", "_stop", "_step", "_count")

    def __init__(self, iterable, /, *args):
        """Initialize the iterator state from the supplied inputs."""
        if not 1 <= len(args) <= 3:
            raise TypeError("islice expected 2 to 4 arguments, got " + str(len(args) + 1))
        start = None
        stop = args[0]
        step = None
        if len(args) > 1:
            start = args[0]
            stop = args[1]
        if len(args) > 2:
            step = args[2]
        if start is None:
            start = 0
        else:
            try:
                start = _ssize(start)
            except BaseException:
                start = -1
        if stop is not None:
            try:
                stop = _ssize(stop)
            except BaseException:
                stop = -1
            if stop == -1:
                raise ValueError(
                    "Stop argument for islice() must be None or an integer: 0 <= x <= sys.maxsize."
                )
        if start < 0 or (stop is not None and stop < 0):
            raise ValueError(
                "Indices for islice() must be None or an integer: 0 <= x <= sys.maxsize."
            )
        if step is None:
            step = 1
        else:
            try:
                step = _ssize(step)
            except BaseException:
                step = -1
        if step < 1:
            raise ValueError("Step for islice() must be a positive integer or None.")
        self._iterator = iter(iterable)
        self._next = start
        self._stop = stop
        self._step = step
        self._count = 0

    def __next__(self):
        """Return the next value, raising StopIteration when exhausted."""
        if self._iterator is None:
            raise StopIteration
        try:
            while self._count < self._next:
                next(self._iterator)
                self._count += 1
            if self._stop is not None and self._count >= self._stop:
                self._iterator = None
                raise StopIteration
            value = next(self._iterator)
        except BaseException:
            self._iterator = None
            raise
        self._count += 1
        self._next += self._step
        if self._stop is not None and self._next > self._stop:
            self._next = self._stop
        return value


class cycle(_Iterator):
    """Cache one traversal of an iterable, then repeat the cached values."""

    __slots__ = ("_iterator", "_saved", "_position")

    def __init__(self, iterable, /):
        """Initialize the iterator state from the supplied inputs."""
        self._iterator = iter(iterable)
        self._saved = []
        self._position = 0

    def __next__(self):
        """Return the next value, raising StopIteration when exhausted."""
        if self._iterator is not None:
            try:
                value = next(self._iterator)
            except StopIteration:
                self._iterator = None
            else:
                self._saved.append(value)
                return value
        if not self._saved:
            raise StopIteration
        value = self._saved[self._position]
        self._position += 1
        if self._position == len(self._saved):
            self._position = 0
        return value


class dropwhile(_Iterator):
    """Skip a true prefix, then pass through the remaining input."""

    __slots__ = ("_predicate", "_iterator", "_dropping")

    def __init__(self, predicate, iterable, /):
        """Initialize the iterator state from the supplied inputs."""
        self._predicate = predicate
        self._iterator = iter(iterable)
        self._dropping = True

    def __next__(self):
        """Return the next value, raising StopIteration when exhausted."""
        while True:
            value = next(self._iterator)
            if not self._dropping:
                return value
            if not self._predicate(value):
                self._dropping = False
                return value


class takewhile(_Iterator):
    """Yield a true prefix and consume its first failing value."""

    __slots__ = ("_predicate", "_iterator", "_stopped")

    def __init__(self, predicate, iterable, /):
        """Initialize the iterator state from the supplied inputs."""
        self._predicate = predicate
        self._iterator = iter(iterable)
        self._stopped = False

    def __next__(self):
        """Return the next value, raising StopIteration when exhausted."""
        if self._stopped:
            raise StopIteration
        value = next(self._iterator)
        if self._predicate(value):
            return value
        self._stopped = True
        raise StopIteration


class filterfalse(_Iterator):
    """Yield inputs whose predicate result is false."""

    __slots__ = ("_predicate", "_iterator")

    def __init__(self, function, iterable, /):
        """Initialize the iterator state from the supplied inputs."""
        self._predicate = function
        self._iterator = iter(iterable)

    def __next__(self):
        """Return the next value, raising StopIteration when exhausted."""
        while True:
            value = next(self._iterator)
            if self._predicate is None or self._predicate is bool:
                keep = not value
            else:
                keep = not self._predicate(value)
            if keep:
                return value


class compress(_Iterator):
    """Yield data paired with true selectors, stopping at the shorter input."""

    __slots__ = ("_data", "_selectors")

    def __init__(self, data, selectors):
        """Initialize the iterator state from the supplied inputs."""
        self._data = iter(data)
        self._selectors = iter(selectors)

    def __next__(self):
        """Return the next value, raising StopIteration when exhausted."""
        while True:
            value = next(self._data)
            if next(self._selectors):
                return value


class starmap(_Iterator):
    """Call a function with each input unpacked as positional arguments."""

    __slots__ = ("_function", "_iterator")

    def __init__(self, function, iterable, /):
        """Initialize the iterator state from the supplied inputs."""
        self._function = function
        self._iterator = iter(iterable)

    def __next__(self):
        """Return the next value, raising StopIteration when exhausted."""
        return self._function(*next(self._iterator))


class accumulate(_Iterator):
    """Yield successive accumulated values, optionally starting with initial."""

    __slots__ = ("_iterator", "_function", "_total", "_initial")

    def __init__(self, iterable, func=None, *, initial=None):
        """Initialize the iterator state from the supplied inputs."""
        self._iterator = iter(iterable)
        self._function = func
        self._total = _missing
        self._initial = initial

    def __next__(self):
        """Return the next value, raising StopIteration when exhausted."""
        if self._initial is not None:
            self._total = self._initial
            self._initial = None
            return self._total
        value = next(self._iterator)
        if self._total is _missing:
            self._total = value
        elif self._function is None:
            self._total = self._total + value
        else:
            self._total = self._function(self._total, value)
        return self._total


class _tee_data:
    __slots__ = ("iterator", "running")

    def __init__(self, iterator):
        """Initialize the iterator state from the supplied inputs."""
        self.iterator = iterator
        self.running = False


class _tee(_Iterator):
    __slots__ = ("_data", "_link", "__weakref__")

    def __init__(self, iterable, /):
        """Initialize the iterator state from the supplied inputs."""
        iterator = iter(iterable)
        if isinstance(iterator, _tee):
            self._data = iterator._data
            self._link = iterator._link
        else:
            self._data = _tee_data(iterator)
            self._link = []

    def __next__(self):
        """Return the next value, raising StopIteration when exhausted."""
        link = self._link
        if not link:
            if self._data.running:
                raise RuntimeError("cannot re-enter the tee iterator")
            self._data.running = True
            try:
                value = next(self._data.iterator)
            finally:
                self._data.running = False
            # Nodes before every live cursor become unreachable immediately.
            link.append(value)
            link.append([])
        self._link = link[1]
        return link[0]

    def __copy__(self):
        return _tee(self)


def tee(iterable, n=2, /):
    """Return independent cursors sharing only the unconsumed input history."""
    n = _ssize(n)
    if n < 0:
        raise ValueError("n must be >= 0")
    if n == 0:
        return ()
    first = _tee(iter(iterable))
    result = [first]
    for _unused in range(n - 1):
        result.append(_tee(first))
    return tuple(result)


class groupby(_Iterator):
    """Group consecutive equal keys with subiterators sharing one input."""

    __slots__ = ("_iterator", "_key", "_value", "_current", "_target", "_active")

    def __init__(self, iterable, key=None):
        """Initialize the iterator state from the supplied inputs."""
        self._iterator = iter(iterable)
        self._key = key
        self._value = _missing
        self._current = _missing
        self._target = _missing
        self._active = 0

    def _step(self):
        value = next(self._iterator)
        key = value if self._key is None else self._key(value)
        self._value = value
        self._current = key

    def __next__(self):
        """Return the next value, raising StopIteration when exhausted."""
        self._active += 1
        while True:
            if self._current is not _missing:
                if self._target is _missing:
                    break
                if self._target is not self._current and not self._target == self._current:
                    break
            groupby._step(self)
        self._target = self._current
        group = _grouper(self, self._target)
        return self._current, group


class _grouper(_Iterator):
    __slots__ = ("_parent", "_target", "_generation")

    def __init__(self, parent, target, /):
        """Initialize the iterator state from the supplied inputs."""
        if not isinstance(parent, groupby):
            raise TypeError("argument 1 must be itertools.groupby, not " + type(parent).__name__)
        self._parent = parent
        self._target = target
        parent._active += 1
        self._generation = parent._active

    def __next__(self):
        """Return the next value, raising StopIteration when exhausted."""
        parent = self._parent
        if parent._active != self._generation:
            raise StopIteration
        if parent._value is _missing:
            groupby._step(parent)
        if self._target is not parent._current and not self._target == parent._current:
            raise StopIteration
        value = parent._value
        parent._value = _missing
        parent._current = _missing
        return value


class zip_longest(_Iterator):
    """Zip inputs until all are exhausted, retaining fillvalue by identity."""

    __slots__ = ("_iterators", "_active", "_fillvalue")

    def __init__(self, *iterables, fillvalue=None):
        """Initialize the iterator state from the supplied inputs."""
        self._iterators = [iter(value) for value in iterables]
        self._active = len(self._iterators)
        self._fillvalue = fillvalue

    def __next__(self):
        """Return the next value, raising StopIteration when exhausted."""
        if not self._active:
            raise StopIteration
        values = []
        for position in range(len(self._iterators)):
            iterator = self._iterators[position]
            if iterator is None:
                value = self._fillvalue
            else:
                try:
                    value = next(iterator)
                except StopIteration:
                    self._active -= 1
                    if not self._active:
                        raise
                    self._iterators[position] = None
                    value = self._fillvalue
                except BaseException:
                    self._active = 0
                    raise
            values.append(value)
        return tuple(values)


class product(_Iterator):
    """Yield Cartesian products using one position per input pool."""

    __slots__ = ("_pools", "_indices", "_first", "_stopped")

    def __init__(self, *iterables, repeat=1):
        """Initialize the iterator state from the supplied inputs."""
        repeat = _ssize(repeat)
        if repeat < 0:
            raise ValueError("repeat argument cannot be negative")
        if repeat == 0:
            pools = []
        else:
            if len(iterables) > _maxsize // 8 // repeat:
                raise OverflowError("repeat argument too large")
            pools = [tuple(value) for value in iterables] * repeat
        self._pools = pools
        self._indices = [0] * len(pools)
        self._first = True
        self._stopped = any(not pool for pool in pools)

    def __next__(self):
        """Return the next value, raising StopIteration when exhausted."""
        if self._stopped:
            raise StopIteration
        if self._first:
            self._first = False
        else:
            position = len(self._pools) - 1
            while position >= 0:
                self._indices[position] += 1
                if self._indices[position] < len(self._pools[position]):
                    break
                self._indices[position] = 0
                position -= 1
            if position < 0:
                self._stopped = True
                raise StopIteration
        return tuple(self._pools[i][self._indices[i]] for i in range(len(self._pools)))


class permutations(_Iterator):
    """Yield ordered selections without repeating input positions."""

    __slots__ = ("_pool", "_r", "_indices", "_cycles", "_first", "_stopped")

    def __init__(self, iterable, r=None):
        """Initialize the iterator state from the supplied inputs."""
        self._pool = tuple(iterable)
        size = len(self._pool)
        if r is None:
            r = size
        else:
            if not isinstance(r, int):
                raise TypeError("Expected int as r")
            r = _ssize(r)
        if r < 0:
            raise ValueError("r must be non-negative")
        self._r = r
        self._indices = list(range(size))
        self._cycles = list(range(size, size - r, -1))
        self._first = True
        self._stopped = r > size

    def __next__(self):
        """Return the next value, raising StopIteration when exhausted."""
        if self._stopped:
            raise StopIteration
        if self._first:
            self._first = False
        else:
            size = len(self._pool)
            position = self._r - 1
            while position >= 0:
                self._cycles[position] -= 1
                if self._cycles[position] == 0:
                    value = self._indices[position]
                    for j in range(position, size - 1):
                        self._indices[j] = self._indices[j + 1]
                    self._indices[size - 1] = value
                    self._cycles[position] = size - position
                    position -= 1
                else:
                    other = size - self._cycles[position]
                    self._indices[position], self._indices[other] = (
                        self._indices[other],
                        self._indices[position],
                    )
                    break
            if position < 0:
                self._stopped = True
                raise StopIteration
        return tuple(self._pool[self._indices[i]] for i in range(self._r))


class combinations(_Iterator):
    """Yield selections in increasing input-position order."""

    __slots__ = ("_pool", "_r", "_indices", "_first", "_stopped")

    def __init__(self, iterable, r):
        """Initialize the iterator state from the supplied inputs."""
        r = _ssize(r)
        self._pool = tuple(iterable)
        if r < 0:
            raise ValueError("r must be non-negative")
        self._r = r
        self._indices = list(range(r))
        self._first = True
        self._stopped = r > len(self._pool)

    def __next__(self):
        """Return the next value, raising StopIteration when exhausted."""
        if self._stopped:
            raise StopIteration
        if self._first:
            self._first = False
        else:
            size = len(self._pool)
            position = self._r - 1
            while position >= 0 and self._indices[position] == position + size - self._r:
                position -= 1
            if position < 0:
                self._stopped = True
                raise StopIteration
            self._indices[position] += 1
            for j in range(position + 1, self._r):
                self._indices[j] = self._indices[j - 1] + 1
        return tuple(self._pool[i] for i in self._indices)


class combinations_with_replacement(_Iterator):
    """Yield nondecreasing selections, allowing repeated input positions."""

    __slots__ = ("_pool", "_indices", "_first", "_stopped")

    def __init__(self, iterable, r):
        """Initialize the iterator state from the supplied inputs."""
        r = _ssize(r)
        self._pool = tuple(iterable)
        if r < 0:
            raise ValueError("r must be non-negative")
        self._indices = [0] * r
        self._first = True
        self._stopped = not self._pool and r != 0

    def __next__(self):
        """Return the next value, raising StopIteration when exhausted."""
        if self._stopped:
            raise StopIteration
        if self._first:
            self._first = False
        else:
            position = len(self._indices) - 1
            while position >= 0 and self._indices[position] == len(self._pool) - 1:
                position -= 1
            if position < 0:
                self._stopped = True
                raise StopIteration
            value = self._indices[position] + 1
            for j in range(position, len(self._indices)):
                self._indices[j] = value
        return tuple(self._pool[i] for i in self._indices)


class pairwise(_Iterator):
    """Yield overlapping pairs, retaining only the previous input value."""

    __slots__ = ("_iterator", "_previous")

    def __init__(self, iterable, /):
        """Initialize the iterator state from the supplied inputs."""
        self._iterator = iter(iterable)
        self._previous = _missing

    def __next__(self):
        """Return the next value, raising StopIteration when exhausted."""
        if self._iterator is None:
            raise StopIteration
        try:
            if self._previous is _missing:
                self._previous = next(self._iterator)
            value = next(self._iterator)
        except BaseException:
            self._iterator = None
            self._previous = _missing
            raise
        result = self._previous, value
        self._previous = value
        return result


class batched(_Iterator):
    """Yield tuples of up to n input values without dropping a short tail."""

    __slots__ = ("_iterator", "_size")

    def __init__(self, iterable, n):
        """Initialize the iterator state from the supplied inputs."""
        n = _ssize(n)
        if n < 1:
            raise ValueError("n must be at least one")
        self._iterator = iter(iterable)
        self._size = n

    def __next__(self):
        """Return the next value, raising StopIteration when exhausted."""
        if self._iterator is None:
            raise StopIteration
        values = []
        for _unused in range(self._size):
            try:
                value = next(self._iterator)
            except StopIteration:
                if not values:
                    self._iterator = None
                    raise
                break
            except BaseException:
                self._iterator = None
                raise
            values.append(value)
        return tuple(values)
