def show(fn):
    try:
        fn()
    except BaseException as e:
        print(f"{type_of(e)}: {e}")


def type_of(e):
    if isinstance(e, ZeroDivisionError):
        return "ZeroDivisionError"
    if isinstance(e, UnboundLocalError):
        return "UnboundLocalError"
    if isinstance(e, IndexError):
        return "IndexError"
    if isinstance(e, KeyError):
        return "KeyError"
    if isinstance(e, AttributeError):
        return "AttributeError"
    if isinstance(e, NameError):
        return "NameError"
    if isinstance(e, ValueError):
        return "ValueError"
    if isinstance(e, TypeError):
        return "TypeError"
    if isinstance(e, StopIteration):
        return "StopIteration"
    return "other"


show(lambda: 1 / 0)
show(lambda: 1 % 0)
show(lambda: 7 // 0)
show(lambda: [1, 2][5])
show(lambda: "ab"[10])
show(lambda: (1,)[9])
show(lambda: {"a": 1}["zz"])
show(lambda: 1 + "s")
show(lambda: "s" + 1)
show(lambda: [] + "x")
show(lambda: len(5))
show(lambda: int("not a number"))
show(lambda: int("12.5"))
show(lambda: float("nope"))
show(lambda: {}[[1, 2]])
show(lambda: {[1]: 2})
show(lambda: "x".index("q"))
show(lambda: [1, 2].index(9))
show(lambda: [].pop())
show(lambda: [1].remove(5))
show(lambda: next(iter([])))
show(lambda: ord("ab"))
show(lambda: chr(-1))


def unpack_few():
    a, b, c = [1, 2]


def unpack_many():
    a, b = [1, 2, 3]


show(unpack_few)
show(unpack_many)


def arity(a, b):
    return a + b


show(lambda: arity(1))
show(lambda: arity(1, 2, 3))
show(lambda: arity(1, z=5))
show(lambda: arity(1, a=1))


def only_kw(*, flag):
    return flag


show(lambda: only_kw())


class Empty:
    pass


show(lambda: Empty(1))
show(lambda: Empty().missing_attr)
show(lambda: "str".missing_method)
show(lambda: (5).foo)
show(lambda: undefined_variable)
show(lambda: -"s")
show(lambda: len(iter([1])) if False else [][0])
