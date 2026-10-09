"""Heavier OOP patterns: cooperative diamond inheritance, observers,
memoizing callable decorator, generator adapters, class factories."""


class Base:
    def __init__(self, **kwargs):
        self.trace = ["Base"]

    def describe(self):
        return "base"


class Persist(Base):
    def __init__(self, store=None, **kwargs):
        super().__init__(**kwargs)
        self.trace.append("Persist")
        self.store = store or []

    def describe(self):
        return "persist(" + super().describe() + ")"


class Audit(Base):
    def __init__(self, auditor="default", **kwargs):
        super().__init__(**kwargs)
        self.trace.append("Audit")
        self.auditor = auditor

    def describe(self):
        return "audit(" + super().describe() + ")"


class Record(Persist, Audit):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.trace.append("Record")

    def describe(self):
        return "record(" + super().describe() + ")"


r = Record(auditor="alice")
print(r.trace)          # C3 order: Base, Audit, Persist, Record
print(r.describe())     # cooperative chain through the diamond
print(r.auditor, r.store)


class EventBus:
    def __init__(self):
        self.handlers = {}

    def on(self, event):
        def register(fn):
            self.handlers.setdefault(event, []).append(fn)
            return fn
        return register

    def emit(self, event, payload):
        results = []
        for fn in self.handlers.get(event, []):
            results.append(fn(payload))
        return results


bus = EventBus()


@bus.on("order.created")
def notify(payload):
    return f"notify:{payload['id']}"


@bus.on("order.created")
def index_order(payload):
    return f"index:{payload['id']}"


@bus.on("order.cancelled")
def refund(payload):
    return f"refund:{payload['id']}"


print(bus.emit("order.created", {"id": 7}))
print(bus.emit("order.cancelled", {"id": 7}))
print(bus.emit("order.shipped", {"id": 7}))


class Memoize:
    def __init__(self, fn):
        self.fn = fn
        self.cache = {}
        self.calls = 0

    def __call__(self, *args):
        if args in self.cache:
            return self.cache[args]
        self.calls += 1
        result = self.fn(*args)
        self.cache[args] = result
        return result


@Memoize
def slow_add(a, b):
    return a + b


@Memoize
def path_count(rows, cols):
    if rows == 1 or cols == 1:
        return 1
    return path_count(rows - 1, cols) + path_count(rows, cols - 1)


print(slow_add(2, 3), slow_add(2, 3), slow_add(4, 5), slow_add.calls)
print(path_count(10, 10), path_count.calls)


def take(it, n):
    for value in it:
        if n <= 0:
            return
        yield value
        n -= 1


def chain(*iterables):
    for it in iterables:
        yield from it


def pairwise(it):
    prev = None
    first = True
    for value in it:
        if not first:
            yield (prev, value)
        prev = value
        first = False


def naturals():
    n = 0
    while True:
        yield n
        n += 1


print(list(take(naturals(), 5)))
print(list(chain([1, 2], take(naturals(), 3), "ab")))
print(list(pairwise([1, 4, 9, 16])))
print(list(take(pairwise(naturals()), 4)))
print(sum(x * y for x, y in take(pairwise(naturals()), 6)))


def make_model(name, fields):
    class Model:
        def __init__(self, **kwargs):
            for field in fields:
                setattr(self, field, kwargs.get(field))

        def __repr__(self):
            inner = ", ".join(f"{f}={getattr(self, f)!r}" for f in fields)
            return f"{name}({inner})"

        def to_dict(self):
            return {f: getattr(self, f) for f in fields}

    return Model


User = make_model("User", ["name", "age"])
Point = make_model("Point", ["x", "y"])
u = User(name="ada", age=36)
p = Point(x=1, y=2)
print(u, p)
print(u.to_dict(), p.to_dict())
print(type(u) is User, isinstance(p, Point), isinstance(p, User))


class Pipeline:
    def __init__(self):
        self.stages = []

    def stage(self, fn):
        self.stages.append(fn)
        return self

    def run(self, value):
        for fn in self.stages:
            value = fn(value)
        return value


result = (Pipeline()
          .stage(lambda words: [w.strip().lower() for w in words])
          .stage(lambda words: [w for w in words if len(w) > 3])
          .stage(sorted)
          .run(["  Apple ", "fig", "Banana", "KIWI ", "date "]))
print(result)
