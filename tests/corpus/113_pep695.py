"""Exercise PEP 695 lexical scopes, lazy evaluation, and generic metadata."""

calls = []


def bound():
    calls.append("bound")
    return int


type Forward = Later
type Recursive = list[Recursive]
type Alias[T: bound(), *Ts, **P] = tuple[T, *Ts]
print(Forward.__name__, Forward.__type_params__)
print(Alias.__name__, [p.__name__ for p in Alias.__type_params__], calls)
T, Ts, P = Alias.__type_params__
print(T.__infer_variance__, T.__bound__ is int, calls)
print(T.__bound__ is int, calls)
print(Recursive.__value__.__args__[0] is Recursive)
Later = str
print(Forward.__value__ is str)
Later = int
print(Forward.__value__ is str)

type Constrained[T: (str, int)] = T
print(Constrained.__type_params__[0].__constraints__ == (str, int))
print(Constrained.__value__ is Constrained.__type_params__[0])

T = "outer"
events = []


def decorator(tag):
    events.append(("decorator", tag))

    def decorate(fn):
        events.append(("apply", fn.__type_params__[0].__name__))
        return fn

    return decorate


def default(value):
    events.append(("default", value))
    return value


@decorator(T)
def identity[T](value: T = default(T)) -> T:
    return value, T


print(events)
print(identity()[0], identity()[1] is identity.__type_params__[0], T)
print(identity.__annotations__["value"] is identity.__type_params__[0])
print(identity.__annotations__["return"] is identity.__type_params__[0])


class Box[T]:
    parameter = T
    item: T
    type Item = T

    def get(self):
        return T


print(Box.__type_params__[0] is Box.parameter)
print(Box.__annotations__["item"] is Box.parameter)
print(Box.Item.__value__ is Box.parameter)
print(Box().get() is Box.parameter)
print(Box[int].__origin__ is Box)
print(Box[int].__args__ == (int,))


class Namespace:
    Value = int
    type A = Value
    type B[T: Value] = T

    def method[T: Value](self):
        return T


print(Namespace.A.__value__ is int)
print(Namespace.B.__type_params__[0].__bound__ is int)
print(Namespace.method.__type_params__[0].__bound__ is int)
print(Namespace().method() is Namespace.method.__type_params__[0])


def enclosing():
    type Local[T] = tuple[T, value]
    value = str
    return Local


Local = enclosing()
print(Local.__value__.__args__[1] is str)
print(Local.__value__.__args__[0] is Local.__type_params__[0])


class LiveNamespace:
    Value = int
    type A = Value
    type B[T: Value] = T

    def ordinary(self):
        return Value

    def generic[T](self):
        return Value


Value = "global"
LiveNamespace.Value = str
print(LiveNamespace.A.__value__ is str)
print(LiveNamespace.B.__type_params__[0].__bound__ is str)
print(LiveNamespace().ordinary(), LiveNamespace().generic())
print("T" in Box.__dict__, "T" in LiveNamespace.__dict__, T)

type Retry = NotYetDefined
try:
    Retry.__value__
except NameError:
    print("lazy retry")
NotYetDefined = int
print(Retry.__value__ is int)


def outer_parameter[T]():
    def inner():
        return T
    return inner


print(outer_parameter()() is outer_parameter.__type_params__[0])

annotation_order = []


def annotation(label):
    annotation_order.append(label)
    return int


def annotated[T](
    value: annotation("value"),
    *args: annotation("args"),
    key: annotation("key"),
    **kwargs: annotation("kwargs"),
) -> annotation("return"):
    """Preserve generic function documentation."""
    return T


print(annotation_order)
print(list(annotated.__annotations__))
print(annotated.__doc__)
