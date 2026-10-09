module_value: int = 3
module_declared: str
print(list(__annotations__), __annotations__["module_value"] is int)
events = []


def mark(name, result):
    events.append(name)
    return result


class Base:
    inherited: int


class Example(Base):
    print("initialized", list(__annotations__))
    first: int
    second: str = "value"
    first: str
    third: mark("annotation", int) = mark("value", 5)
    if True:
        branch: bool
    if False:
        absent: bytes
    for i in range(2):
        repeated: int

    def method(self, arg: int) -> int:
        local: ignored_name = arg
        return local


print(list(Example.__annotations__))
print(Example.__annotations__["first"] is str)
print(Example.__annotations__["third"] is int)
print(Example.second, Example.third, hasattr(Example, "first"))
print(events, Example().method(7))
print(list(Base.__annotations__))


class Explicit:
    __annotations__ = {"before": float}
    after: int = 2


print(list(Explicit.__annotations__), Explicit.after)


class Nested:
    own: int

    class Inner:
        inner: str


print(list(Nested.__annotations__), list(Nested.Inner.__annotations__))


class Holder:
    pass


holder = Holder()
items = {}
events = []


class NonSimple:
    (parenthesized): mark("paren", int)
    mark("object", holder).x: mark("attr", str)
    items[mark("index", "key")]: mark("indexed", bool)
    holder.value: mark("assigned attr", int) = mark("assigned value", 9)


print(events)
print(list(NonSimple.__annotations__), holder.value, items)
