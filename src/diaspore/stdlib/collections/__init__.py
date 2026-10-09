"""Provide namedtuple, OrderedDict, defaultdict, Counter, and deque."""


class OrderedDict(dict):
    def move_to_end(self, key, last=True):
        value = self[key]
        del self[key]
        if last:
            self[key] = value
        else:
            items = list(self.items())
            self.clear()
            self[key] = value
            for k, v in items:
                self[k] = v

    def popitem(self, last=True):
        if not self:
            raise KeyError("dictionary is empty")
        keys = list(self.keys())
        key = keys[-1] if last else keys[0]
        value = self[key]
        del self[key]
        return key, value

    def __repr__(self):
        if not self:
            return "OrderedDict()"
        return "OrderedDict(" + repr(dict(self)) + ")"


class defaultdict(dict):
    def __init__(self, default_factory=None, *args, **kwargs):
        self.default_factory = default_factory
        for a in args:
            self.update(a)
        self.update(kwargs)

    def __getitem__(self, key):
        if key in self:
            return dict.__getitem__(self, key)
        return self.__missing__(key)

    def __missing__(self, key):
        if self.default_factory is None:
            raise KeyError(key)
        value = self.default_factory()
        self[key] = value
        return value

    def __repr__(self):
        return f"defaultdict({self.default_factory!r}, {dict(self)!r})"


class Counter(dict):
    def __init__(self, iterable=None, **kwargs):
        if iterable is not None:
            if isinstance(iterable, dict):
                for k, v in iterable.items():
                    self[k] = self.get(k, 0) + v
            else:
                for elem in iterable:
                    self[elem] = self.get(elem, 0) + 1
        for k, v in kwargs.items():
            self[k] = self.get(k, 0) + v

    def __missing__(self, key):
        return 0

    def most_common(self, n=None):
        items = sorted(self.items(), key=lambda kv: -kv[1])
        return items if n is None else items[:n]

    def elements(self):
        for elem, count in self.items():
            for _ in range(count):
                yield elem

    def update(self, other):
        if isinstance(other, dict):
            for k, v in other.items():
                self[k] = self.get(k, 0) + v
        else:
            for elem in other:
                self[elem] = self.get(elem, 0) + 1

    def total(self):
        return sum(self.values())


class deque:
    def __init__(self, iterable=None, maxlen=None):
        self._items = list(iterable) if iterable is not None else []
        self.maxlen = maxlen
        if maxlen is not None:
            self._items = self._items[-maxlen:]

    def append(self, x):
        self._items.append(x)
        if self.maxlen is not None and len(self._items) > self.maxlen:
            self._items.pop(0)

    def appendleft(self, x):
        self._items.insert(0, x)
        if self.maxlen is not None and len(self._items) > self.maxlen:
            self._items.pop()

    def pop(self):
        if not self._items:
            raise IndexError("pop from an empty deque")
        return self._items.pop()

    def popleft(self):
        if not self._items:
            raise IndexError("pop from an empty deque")
        return self._items.pop(0)

    def extend(self, it):
        for x in it:
            self.append(x)

    def clear(self):
        self._items = []

    def __len__(self):
        return len(self._items)

    def __iter__(self):
        return iter(self._items)

    def __getitem__(self, i):
        return self._items[i]

    def __repr__(self):
        return f"deque({self._items!r})"


def namedtuple(typename, field_names):
    if isinstance(field_names, str):
        field_names = field_names.replace(",", " ").split()
    field_names = list(field_names)

    class NamedTuple:
        _fields = tuple(field_names)

        def __init__(self, *args, **kwargs):
            if len(args) + len(kwargs) != len(field_names):
                raise TypeError(f"{typename} takes {len(field_names)} arguments")
            self._values = list(args)
            for name in field_names[len(args):]:
                self._values.append(kwargs[name])
            for name, val in zip(field_names, self._values):
                setattr(self, name, val)

        def __iter__(self):
            return iter(self._values)

        def __getitem__(self, i):
            return self._values[i]

        def __len__(self):
            return len(self._values)

        def __eq__(self, other):
            if isinstance(other, NamedTuple):
                return self._values == other._values
            return tuple(self._values) == other

        def _replace(self, **kwargs):
            vals = dict(zip(field_names, self._values))
            vals.update(kwargs)
            return NamedTuple(*[vals[f] for f in field_names])

        def _asdict(self):
            return dict(zip(field_names, self._values))

        def __repr__(self):
            inner = ", ".join(f"{n}={v!r}" for n, v in zip(field_names, self._values))
            return f"{typename}({inner})"

    NamedTuple.__name__ = typename
    return NamedTuple
