"""Scoping esoterica: class bodies, comprehension scopes, nonlocal chains."""

CONST = "module"


class ScopeGames:
    CONST = "class"
    seen_module = CONST  # class body sees its own binding once made
    xs = [1, 2, 3]
    ys = [x * 10 for x in xs]  # iterable evaluated IN class scope

    def method(self):
        return CONST  # methods skip class scope -> module


print(ScopeGames.seen_module, ScopeGames.ys, ScopeGames().method(), CONST)

try:
    class BadComp:
        limit = 5
        # comprehension BODY cannot see class scope in CPython
        vals = [limit for _ in range(2)]
except NameError as e:
    print("class-scope-in-comp:", e)


def nonlocal_chain():
    a = "L1"

    def mid():
        nonlocal_probe = a  # read outer

        def deep():
            nonlocal a
            a = "L3-wrote"
        deep()
        return nonlocal_probe
    before = mid()
    return before, a


print(nonlocal_chain())


def make_counters():
    fns = []
    for i in range(3):
        def cap(j=i):
            return (i, j)
        fns.append(cap)
    return [f() for f in fns]


print(make_counters())


def except_shadow():
    e = "outer-e"
    try:
        raise ValueError("boom")
    except ValueError as e:
        inner = str(e)
    # e is DELETED after the handler (not restored to "outer-e")
    try:
        return inner, e
    except NameError:
        return inner, "e was unbound"


print(except_shadow())

g = 100


def rebind_global_reader():
    def reader():
        return g

    def writer():
        global g
        g = g + 1
    writer()
    return reader()


print(rebind_global_reader(), g)


class Shadow:
    attr = "class-attr"

    def hide(self):
        self.attr = "instance-attr"
        return Shadow.attr, self.attr


s = Shadow()
print(s.hide())
print(Shadow.attr, s.attr)
del s.attr
print(s.attr)  # falls back to class


def loop_del():
    items = list(range(5))
    total = 0
    for v in items:
        total += v
        if v == 2:
            del v  # legal: v rebound next iteration
    return total


print(loop_del())


def comp_sees_locals():
    base = 10
    return [base + n for n in range(3)]


print(comp_sees_locals())


def gen_closure():
    n = 5

    def g():
        yield n
        yield n * 2
    out = list(g())
    n = 7  # after exhaustion: no effect on captured results
    return out, n


print(gen_closure())

lam_default_scope = [(lambda x=CONST: x)() for _ in range(2)]
print(lam_default_scope)


def double_walrus():
    data = [3, 1, 2]
    if (n := len(data)) and (m := max(data)):
        return n, m, n * m


print(double_walrus())
print("done")
