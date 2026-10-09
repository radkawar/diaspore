"""Legal-but-weird syntax the compiler must lower correctly."""


def deco_a(fn):
    return lambda *a: f"A[{fn(*a)}]"


def deco_b(tag):
    def inner(fn):
        return lambda *a: f"B{tag}[{fn(*a)}]"
    return inner


@deco_a
@deco_b("x")
@deco_b("y")
def stacked(v):
    return v * 2


print(stacked(5))


def defaults_gauntlet(a=len("abc"), b=[i for i in range(3)], c=(lambda: 9)(),
                      d={"k": [1, 2]}, e=1 if True else 2):
    return a, b, c, d["k"], e


print(defaults_gauntlet())
print(defaults_gauntlet(0))
r1 = defaults_gauntlet()
r2 = defaults_gauntlet()
print(r1[1] is r2[1])  # shared mutable default


class Outer:
    class Inner:
        value = "nested class"

        def get(self):
            return Outer.Inner.value

    def make(self):
        class Local:
            def __init__(self, n):
                self.n = n * 2
        return Local(21)


print(Outer.Inner().get(), Outer().make().n)


def f_in_c_in_f():
    x = 10

    class C:
        y = x + 1

        def m(self):
            return x + 100
    return C


K = f_in_c_in_f()
print(K.y, K().m())

print(1 < 2 < 3 < 4 < 5, 5 > 4 > 3 > 2 > 1, 1 < 2 > 1 < 2 > 1)
print((lambda a, /, b, *, c: a + b + c)(1, 2, c=3) if False else
      (lambda a, b, c=3: a + b + c)(1, 2))
print([(x, y) for x in range(2) for y in range(x + 1)],
      [[y for y in range(x)] for x in range(3)])
print({k: {v: k for v in range(k)} for k in range(3)})
w = [1, 2, 3]
print((walrus_out := [v for v in w]), w, walrus_out is not w)

vals = [x
        for x in
        range(4)
        if x
        != 2]
print(vals)

s = ("implicit"
     " concat"
     ' works')
print(s, len(s))
print("a" "b" f"c{1}" "d")

cond = lambda n: ("neg" if n < 0
                  else "zero" if n == 0
                  else "pos")
print(cond(-1), cond(0), cond(1))


def star_madness(*args, **kw):
    return args, sorted(kw.items())


packed = [1, 2]
extra = {"m": 9}
print(star_madness(*packed, 3, *[4], k=1, **extra, j=2))
print([*packed, 5, *(6, 7)], {**extra, "n": 1}, (*packed, 9))

matrix = [[1, 2], [3, 4]]
(a, b), (c, d) = matrix
print(a, b, c, d)
[[e, f], [g, h]] = matrix
print(e + f + g + h)

x = y = z = [0]
x.append(1)
print(x is y is z, z)

print(-2 ** 2, (-2) ** 2, 2 ** -1, -2 ** -1)
print(not True == False, (not True) == False, not (True == False))
print(5 if not [] else 6, [] or {} or "last", 0 and 1 or 2)

try:
    try:
        raise ValueError("inner")
    finally:
        print("f1")
except ValueError as ve:
    print("outer got:", ve)


def ret_in_except():
    try:
        raise KeyError("k")
    except KeyError:
        return "from except"
    finally:
        print("f2")


print(ret_in_except())
print("done")
