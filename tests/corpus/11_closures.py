def make_counter(start=0):
    n = start

    def bump(by=1):
        nonlocal n
        n += by
        return n

    def peek():
        return n

    return bump, peek


bump, peek = make_counter(100)
print(bump(), bump(5), peek())
bump2, peek2 = make_counter()
print(bump2(), peek(), peek2())

total = 0


def add_global(v):
    global total
    total += v


add_global(3)
add_global(4)
print(total)

fns = []
for i in range(3):
    fns.append(lambda: i)
print([f() for f in fns])

fns2 = [lambda i=i: i for i in range(3)]
print([f() for f in fns2])


def outer():
    x = "outer"

    def middle():
        def inner():
            return x
        return inner()
    return middle()


print(outer())


def accumulate():
    items = []

    def push(v):
        items.append(v)
        return items
    return push


p = accumulate()
p(1)
p(2)
print(p(3))


def trace(fn):
    def wrapper(*args):
        r = fn(*args)
        print(f"call {fn_name(fn)}{args} -> {r}")
        return r
    return wrapper


def fn_name(f):
    return "double"


@trace
def double(x):
    return x * 2


double(7)
double(21)


def repeat(times):
    def deco(fn):
        def wrapper(arg):
            out = []
            for _ in range(times):
                out.append(fn(arg))
            return out
        return wrapper
    return deco


@repeat(3)
def shout(s):
    return s.upper()


print(shout("hey"))


def compose(f, g):
    return lambda v: f(g(v))


inc = lambda v: v + 1
sq = lambda v: v * v
print(compose(inc, sq)(4), compose(sq, inc)(4))
