import contextlib


@contextlib.contextmanager
def tag(name):
    print(f"<{name}>")
    yield name.upper()
    print(f"</{name}>")


with tag("div") as t:
    print("body", t)


@contextlib.contextmanager
def guarded():
    print("acquire")
    try:
        yield
    finally:
        print("release")


try:
    with guarded():
        print("work")
        raise ValueError("boom")
except ValueError as e:
    print("caught", e)

with contextlib.suppress(ZeroDivisionError):
    print("before")
    1 / 0
    print("unreached")
print("after suppress")


class Res:
    def __init__(self, n):
        self.n = n
    def close(self):
        print("closed", self.n)


with contextlib.closing(Res(1)) as r:
    print("using", r.n)

with contextlib.nullcontext(42) as v:
    print("null", v)

with contextlib.ExitStack() as stack:
    stack.enter_context(tag("a"))
    stack.enter_context(tag("b"))
    stack.callback(lambda: print("callback fired"))
    print("in stack")
print("stack done")


@contextlib.contextmanager
def numbers():
    yield [1, 2, 3]


with numbers() as nums:
    print("sum", sum(nums))
print("done")
