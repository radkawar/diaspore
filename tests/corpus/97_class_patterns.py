events = []


class Point:
    __match_args__ = ("x", "y")

    def __init__(self, x, y):
        self._x = x
        self._y = y

    @property
    def x(self):
        events.append("x")
        return self._x

    @property
    def y(self):
        events.append("y")
        return self._y


class Child(Point):
    pass


match Child(2, 3):
    case Point(a, y=b) if a + b == 5:
        print("inherited", a, b, events)

events.clear()
match Point(2, 3):
    case Point(99, _):
        print("unreachable")
    case Point(x=a):
        print("extract before matching", a, events)

Point.__match_args__ = ("y", "x")
events.clear()
match Point(2, 3):
    case Point(a, b):
        print("live match args", a, b, events)

try:
    match Point(2, 3):
        case Point(a, b, c):
            pass
except TypeError as exc:
    print(str(exc))

try:
    match Point(2, 3):
        case Point(a, y=b):
            pass
except TypeError as exc:
    print(str(exc))

Point.__match_args__ = ("x", "x")
try:
    match Point(2, 3):
        case Point(a, b):
            pass
except TypeError as exc:
    print(str(exc))

for invalid in (["x"], (1,), None):
    Point.__match_args__ = invalid
    try:
        match Point(2, 3):
            case Point(a):
                pass
    except TypeError as exc:
        print(str(exc))


class TupleChild(tuple):
    pass


class StringChild(str):
    pass


for invalid in (TupleChild(("x",)), (StringChild("x"),)):
    Point.__match_args__ = invalid
    try:
        match Point(2, 3):
            case Point(a):
                pass
    except TypeError as exc:
        print(str(exc))

Point.__match_args__ = ["x"]
match 12:
    case Point(a):
        print("unreachable")
    case _:
        print("class mismatch skips match args")

match Point(5, 6):
    case Point(x=a):
        print("keyword skips match args", a)


class Missing:
    __match_args__ = ("absent",)


match Missing():
    case Missing(a):
        print("unreachable")
    case _:
        print("missing attribute does not match")


class Broken:
    __match_args__ = ("value",)

    @property
    def value(self):
        raise ValueError("getter failure")


try:
    match Broken():
        case Broken(a):
            pass
except ValueError as exc:
    print(str(exc))

Point.__match_args__ = ("x",)
try:
    match Point(5, 6):
        case Point(a):
            raise AttributeError("body failure")
except AttributeError as exc:
    print(str(exc))

NotClass = (Point,)
try:
    match Point(5, 6):
        case NotClass():
            pass
except TypeError as exc:
    print(str(exc))

for Self, obj in ((bool, True), (bytearray, bytearray(b"a")), (bytes, b"b"),
                  (dict, {"a": 1}), (float, 2.5), (frozenset, frozenset()),
                  (int, 8), (list, [9]), (set, set()), (str, "s"), (tuple, (10,))):
    match obj:
        case Self(value):
            print("match self", Self.__name__, value is obj)

match 9:
    case int(value, real=real):
        print("self and keyword", value, real)

try:
    match 9:
        case int(a, b):
            pass
except TypeError as exc:
    print(str(exc))

match StringChild("inherited"):
    case StringChild(value):
        print("inherited self", value)

StringChild.__match_args__ = ()
try:
    match StringChild("overridden"):
        case StringChild(value):
            pass
except TypeError as exc:
    print(str(exc))


def shadowed(type, tuple, str, isinstance, issubclass, getattr, TypeError, AttributeError):
    match Point(7, 8):
        case Point(value):
            return value


print("unshadowable builtins", shadowed(None, None, None, None, None, None, None, None))

value = "hello"
number = 12
print(f"debug {value=}; {number = }")
print(f"{value=!s} {value=!a} {number=:04d}")
print(f"{number == 12=} {number != 13=} {number >= 12=} {number <= 12=}")
print(f"{(number := 14)=} {number=}")
print(f"nested {number=:{4}d}")


def positional(value=10, /, named=20, **kwargs):
    return value, named, kwargs


def strict(value, /):
    return value


class PositionalMethod:
    def method(self, value, /, **kwargs):
        return value, kwargs


print("positional keyword capture", positional(1, value=2))
print("positional default", positional(value=3, named=4))
print("bound positional", PositionalMethod().method(5, value=6))
only = lambda value, /: value + 1
print("positional lambda", only(7))
print("positional metadata", positional.__code__.co_posonlyargcount,
      strict.__code__.co_posonlyargcount, only.__code__.co_posonlyargcount,
      PositionalMethod.method.__code__.co_posonlyargcount)
try:
    strict(value=8)
except TypeError:
    print("positional keyword rejected")
try:
    only(value=9)
except TypeError:
    print("positional lambda keyword rejected")


async def coroutine_kind():
    return 1


async def async_generator_kind():
    yield await coroutine_kind()


async def nested_generator_kind():
    def nested():
        yield 2
    return 3


async def default_generator_kind():
    def nested(value=(yield 4)):
        return value


print("function kind flags", coroutine_kind.__code__.co_flags & 640,
      async_generator_kind.__code__.co_flags & 640,
      nested_generator_kind.__code__.co_flags & 640,
      default_generator_kind.__code__.co_flags & 640)
