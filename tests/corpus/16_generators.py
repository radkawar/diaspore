def countdown(n):
    while n > 0:
        yield n
        n -= 1
    return "liftoff"


print(list(countdown(5)))

g = countdown(3)
print(next(g), next(g), next(g))
try:
    next(g)
except StopIteration:
    print("exhausted")
print(next(countdown(0), "empty"))


def evens():
    n = 0
    while True:
        yield n
        n += 2


e = evens()
print([next(e) for _ in range(6)])


def chain(*iterables):
    for it in iterables:
        yield from it


print(list(chain([1, 2], "ab", (3,))))


def inner_gen():
    yield 1
    yield 2
    return "inner done"


def outer_gen():
    result = yield from inner_gen()
    yield f"got: {result}"


print(list(outer_gen()))


def squares(limit):
    for i in range(limit):
        yield i * i


print(sum(squares(10)), max(squares(5)))
print([s for s in squares(6) if s % 2 == 0])
print(list(zip(squares(4), "abcd")))
a, b, c = squares(3)
print(a, b, c)


def running_total(nums):
    total = 0
    for n in nums:
        total += n
        yield total


print(list(running_total([1, 2, 3, 4])))
print(list(running_total(squares(5))))


def flatten(tree):
    for node in tree:
        if isinstance(node, list):
            yield from flatten(node)
        else:
            yield node


print(list(flatten([1, [2, [3, 4], 5], [6]])))


def take(it, n):
    out = []
    for v in it:
        if len(out) >= n:
            break
        out.append(v)
    return out


fib_gen = None


def fibs():
    a, b = 0, 1
    while True:
        yield a
        a, b = b, a + b


print(take(fibs(), 10))

gen_expr = (c * 2 for c in "xyz")
print(next(gen_expr), list(gen_expr))


def echo_twice(source):
    for item in source:
        yield item
        yield item


print(list(echo_twice(["a", "b"])))
print(list(echo_twice(countdown(2))))
