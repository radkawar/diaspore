def add(a, b=10, c=100):
    return a + b + c

print(add(1), add(1, 2), add(1, 2, 3), add(1, c=5), add(c=1, a=2, b=3))

def collect(*args, **kwargs):
    return args, sorted(kwargs.items())

print(collect(), collect(1, 2, x=3, y=4))

def mixed(a, b, *rest, sep="-", cap=False, **extra):
    body = sep.join([str(a), str(b)] + [str(r) for r in rest])
    if cap:
        body = body.upper()
    return body, sorted(extra.items())

print(mixed(1, 2))
print(mixed(1, 2, 3, 4, sep="+"))
print(mixed("a", "b", "c", cap=True, tag="x"))
args = [10, 20, 30]
kw = {"sep": "*", "note": "spread"}
print(mixed(*args, **kw))
print(mixed(*[1, 2], *[3], **{"cap": True}))

def kwonly(*, key, flag=True):
    return key, flag

print(kwonly(key="k"), kwonly(key="k", flag=False))

def fib(n):
    if n < 2:
        return n
    return fib(n - 1) + fib(n - 2)

print([fib(i) for i in range(12)])

def is_even(n):
    return n == 0 or is_odd(n - 1)

def is_odd(n):
    return n != 0 and is_even(n - 1)

print(is_even(10), is_odd(7), is_even(3))

def nothing():
    pass

print(nothing())

def early(n):
    if n < 0:
        return "neg"
    if n == 0:
        return "zero"
    return "pos"

print(early(-5), early(0), early(5))

square = lambda v: v * v
print(square(9), (lambda a, b=2: a ** b)(3), (lambda: 42)())

def apply(fn, val):
    return fn(val)

print(apply(square, 6), apply(len, "hello"), apply(abs, -3))

def make_default(item, bucket=None):
    if bucket is None:
        bucket = []
    bucket.append(item)
    return bucket

print(make_default(1), make_default(2), make_default(3, [0]))
