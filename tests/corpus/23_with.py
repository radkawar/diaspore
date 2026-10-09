class Resource:
    def __init__(self, name, fail=False):
        self.name = name
        self.fail = fail

    def __enter__(self):
        print("enter", self.name)
        return self

    def __exit__(self, exc_type, exc, tb):
        print("exit", self.name, exc_type is None)
        return False

    def work(self):
        if self.fail:
            raise RuntimeError(f"{self.name} failed")
        return f"{self.name} ok"


with Resource("db") as r:
    print(r.work())

try:
    with Resource("net", fail=True) as r:
        print(r.work())
except RuntimeError as e:
    print("escaped:", e)


class Suppress:
    def __init__(self, *types):
        self.types = types

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            return False
        for t in self.types:
            if isinstance(exc, t):
                print("suppressed", exc)
                return True
        return False


with Suppress(ValueError):
    raise ValueError("swallowed")
print("continues")

with Suppress(KeyError, IndexError):
    [1, 2][99]
print("still going")

try:
    with Suppress(KeyError):
        raise TypeError("not suppressed")
except TypeError as e:
    print("passed through:", e)

with Resource("outer"), Resource("inner") as inner:
    print("body", inner.name)


class Timer:
    def __enter__(self):
        self.events = ["start"]
        return self.events

    def __exit__(self, *exc):
        self.events.append("stop")
        return False


t = Timer()
with t as events:
    events.append("work")
print(t.events)

with Resource("a") as x, Suppress(ValueError) as y:
    print("multi", x.name)
    raise ValueError("inner suppressed")
print("done")


def early_return():
    with Resource("ret"):
        return "returned"


print(early_return())

for i in range(3):
    with Resource(f"loop{i}"):
        if i == 1:
            break
        if i == 0:
            continue
print("after loop", i)


def generator_with():
    with Resource("gen"):
        yield 1
        yield 2


print(list(generator_with()))
print("exit ran on return/break/continue paths")
