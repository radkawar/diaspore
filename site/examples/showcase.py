"""Classes, pattern matching, generators and comprehensions."""


class Vec:
    def __init__(self, x, y):
        self.x = x
        self.y = y

    def __add__(self, other):
        return Vec(self.x + other.x, self.y + other.y)

    def __repr__(self):
        return f"Vec({self.x}, {self.y})"


def fib():
    a, b = 0, 1
    while True:
        yield a
        a, b = b, a + b


def take(gen, n):
    out = []
    for v in gen:
        if len(out) == n:
            break
        out.append(v)
    return out


def describe(value):
    match value:
        case 0:
            return "zero"
        case int() if value < 0:
            return "negative"
        case [x, y]:
            return f"pair of {x} and {y}"
        case {"kind": kind}:
            return f"a {kind} record"
        case str() as s:
            return f"string of length {len(s)}"
        case _:
            return "something else"


print(f"vectors: {Vec(1, 2) + Vec(3, 4)}")
print("fib:", take(fib(), 10))
print("big fib:", take(fib(), 120)[-1])
for probe in [0, -7, [1, 2], {"kind": "order"}, "hello", 3.5]:
    print(f"{probe!r:>18} -> {describe(probe)}")
squares = {n: n * n for n in range(1, 6)}
print("squares:", squares, "| sum:", sum(squares.values()))
