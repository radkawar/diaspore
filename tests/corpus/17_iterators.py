class Countdown:
    def __init__(self, n):
        self.n = n

    def __iter__(self):
        return CountdownIter(self.n)


class CountdownIter:
    def __init__(self, n):
        self.n = n

    def __iter__(self):
        return self

    def __next__(self):
        if self.n <= 0:
            raise StopIteration
        self.n -= 1
        return self.n + 1


c = Countdown(4)
print(list(c), list(c))
for v in Countdown(3):
    print("tick", v)
it = iter(Countdown(2))
print(next(it), next(it), next(it, "done"))
x, y = Countdown(2)
print(x, y)


class Fibs:
    def __init__(self, limit):
        self.a, self.b, self.limit = 0, 1, limit

    def __iter__(self):
        return self

    def __next__(self):
        if self.a > self.limit:
            raise StopIteration
        v = self.a
        self.a, self.b = self.b, self.a + self.b
        return v


print(list(Fibs(50)), sum(Fibs(10)))
print([n for n in Fibs(20) if n % 2 == 0])

e = enumerate("abc")
print(next(e), list(e))
z = zip([1, 2, 3], "xy")
print(list(z), list(z))
m = map(lambda v: v + 1, [10, 20])
print(next(m), list(m))
f = filter(lambda v: v > 1, [0, 1, 2, 3])
print(list(f))
print(list(map(lambda a, b: a * b, [1, 2, 3], [4, 5, 6])))
print(list(filter(None, [0, 1, "", "x", None, [], [1]])))
print(list(enumerate("ab", 10)))
print(list(reversed([1, 2, 3])), list(reversed("abc")), list(reversed(range(4))))
print(list(zip(Fibs(5), Countdown(3))))
r = iter(range(3))
print(next(r), next(r), next(r, None), next(r, None))
print(list(iter([7, 8])), list(iter("hi")))
