"""Adversarial edges: dunders that raise, weird-but-legal protocol use."""


class BadRepr:
    def __repr__(self):
        raise ValueError("repr exploded")


class BadEq:
    def __eq__(self, other):
        raise RuntimeError("eq exploded")


class BadBool:
    def __bool__(self):
        raise TypeError("bool exploded")


class BadIter:
    def __iter__(self):
        raise KeyError("iter exploded")


class LyingLen:
    def __len__(self):
        return 5


def probe(label, fn):
    try:
        print(label, fn())
    except BaseException as e:
        print(label, "->", type_label(e), str(e))


def type_label(e):
    for cls in (ValueError, RuntimeError, TypeError, KeyError, ZeroDivisionError,
                StopIteration, IndexError, AttributeError):
        if isinstance(e, cls) and type(e) is cls:
            return cls_name(cls)
    return "other"


def cls_name(c):
    return str(c).split("'")[1]


probe("repr-in-list", lambda: [BadRepr()])
probe("repr-in-fstring", lambda: f"{BadRepr()!r}")
probe("eq-in-compare", lambda: BadEq() == 1)
probe("eq-in-list-contains", lambda: BadEq() in [1, 2])
probe("bool-in-if", lambda: "yes" if BadBool() else "no")
probe("bool-of-lyinglen", lambda: bool(LyingLen()))
probe("iter-raises", lambda: list(BadIter()))
probe("sort-key-raises", lambda: sorted([3, 1, 2], key=lambda x: 1 // (x - 1)))
probe("range-step-zero", lambda: range(1, 5, 0))
probe("slice-step-zero", lambda: [1, 2, 3][::0])
probe("empty-range-max", lambda: max(range(0)))
probe("str-mul-negative", lambda: "ab" * -3)
probe("list-mul-negative", lambda: [1] * -2)
probe("chr-big", lambda: chr(97) + chr(0x10FFFF) == "a" + chr(1114111))
probe("nested-fstring-spec", lambda: f"{3.14159:{'>'}{10}.{2}f}")
probe("deep-parens", lambda: ((((((((((1)))))))))) + 1)
probe("unicode-ident", lambda: True)
naïve_β = 42
probe("unicode-var", lambda: naïve_β)
probe("empty-fstring", lambda: f"")
probe("format-weird-fill", lambda: format(7, "±^7") if False else format(7, "x^7"))


def gen_raises():
    yield 1
    raise ValueError("mid-generator")
    yield 2


probe("gen-raises-mid", lambda: list(gen_raises()))


def gen_in_finally():
    try:
        yield 1
    finally:
        print("gen finally ran")


probe("gen-finally-exhausted", lambda: list(gen_in_finally()))


class SelfAttr:
    def __getattr__(self, n):
        if n == "chain":
            return self
        raise AttributeError(n)


s = SelfAttr()
probe("getattr-chain", lambda: s.chain.chain.chain is s)
probe("getattr-miss", lambda: s.nope)

d = {}
d[True] = "bool-key"
d[1] = "int-key"
d[1.0] = "float-key"
print("key-unify:", d, len(d))
probe("dict-del-missing", lambda: d.pop("absent"))

t = (1, [2, 3])
probe("tuple-mutable-inside", lambda: (t[1].append(4), t)[1])
probe("aug-on-tuple-item", lambda: exec_augfail())


def exec_augfail():
    tt = (1, 2)
    try:
        tt[0] += 1
    except TypeError as e:
        return f"caught: {e}"


probe("min-key-none-vs", lambda: min([3, 1, 2], key=None) if False else min([3, 1, 2]))
probe("zip-unequal", lambda: list(zip([1, 2, 3], "ab", [9, 8, 7, 6])))
probe("enumerate-neg-start", lambda: list(enumerate("ab", -5)))
print("done")
