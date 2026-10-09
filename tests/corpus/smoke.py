def greet(name, punct="!"):
    return f"hello, {name}{punct}"

class Counter:
    def __init__(self, start=0):
        self.n = start
    def bump(self, by=1):
        self.n += by
        return self.n
    def __repr__(self):
        return f"Counter({self.n})"

c = Counter()
for i in range(5):
    c.bump(i)
print(greet("diaspore"), c, c.n)
xs = [x * x for x in range(10) if x % 2 == 0]
print(xs, sum(xs), max(xs, key=lambda v: -v))
d = {"a": 1, "b": 2}
print(sorted(d.items()), {v: k for k, v in d.items()})

def fib():
    a, b = 0, 1
    while True:
        yield a
        a, b = b, a + b

g = fib()
print([next(g) for _ in range(10)])
try:
    1 / 0
except ZeroDivisionError as e:
    print("caught:", e)
finally:
    print("done")
