from __future__ import annotations

from collections.abc import Callable
from dataclasses import FrozenInstanceError, dataclass, field, fields, is_dataclass, replace


@dataclass
class Packet:
    name: str
    count: int = 2
    values: list = field(default_factory=list)
    hidden: int = field(default=9, repr=False, compare=False)
    total: int = field(default=0, init=False)

    def __post_init__(self):
        self.total = self.count * 3


first = Packet("one")
second = Packet(name="one")
print(first, second, first == second)
first.values.append(7)
print(first.values, second.values, first == second)
print(Packet("one", hidden=4) == Packet("one", hidden=8))
print(replace(second, count=5))
print([(item.name, item.init, item.repr, item.compare) for item in fields(Packet)])
print(is_dataclass(Packet), is_dataclass(first), is_dataclass(3))
print(Packet.__match_args__)
print(hasattr(Packet, "values"), Packet.count)
for build in (lambda: Packet(), lambda: Packet("x", name="y"), lambda: Packet(unknown=1)):
    try:
        build()
    except TypeError as error:
        print(type(error).__name__)


@dataclass(frozen=True)
class Codec:
    name: str
    extensions: tuple[str, ...]
    sniff: Callable[[bytes], bool]
    decode: Callable[[bytes], bytes]
    encode: Callable[[bytes, bool], bytes]


def identity(data):
    return data


def encode(data, pretty):
    return data + (b"\n" if pretty else b"")


codec = Codec(name="raw", extensions=(".raw",), sniff=lambda data: bool(data), decode=identity, encode=encode)
print(codec.name, codec.extensions, codec.sniff(b"x"), codec.decode(b"x"), codec.encode(b"x", True))
for change in (lambda: setattr(codec, "name", "new"), lambda: setattr(codec, "other", 1)):
    try:
        change()
    except FrozenInstanceError as error:
        print(type(error).__name__, str(error))
try:
    del codec.name
except FrozenInstanceError as error:
    print(type(error).__name__, str(error))
print(codec.name)


@dataclass(frozen=True)
class Point:
    x: int
    y: int = 0


print(Point(1), Point(1) == Point(x=1), Point(1) != Point(2))
print(Point(1) == (1, 0), hash(Point(1)) == hash(Point(1)))
print(replace(Point(1), y=4))
try:
    hash(first)
except TypeError as error:
    print(type(error).__name__)


@dataclass
class FactoryOnly:
    items: list = field(default_factory=list, init=False)


a = FactoryOnly()
b = FactoryOnly()
a.items.append("separate")
print(a, b)


@dataclass(repr=False, eq=False)
class Manual:
    value: int

    def __repr__(self):
        return "manual:" + str(self.value)


print(Manual(4), Manual(4) == Manual(4))
try:
    @dataclass
    class BadDefault:
        items: list = []
except ValueError as error:
    print(type(error).__name__)
try:
    @dataclass
    class BadOrder:
        first: int = 1
        second: int
except TypeError as error:
    print(type(error).__name__, str(error))
