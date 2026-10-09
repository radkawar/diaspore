class Legacy(object):
    def tag(self):
        return "legacy"


class Modern:
    def tag(self):
        return "modern"


l, m = Legacy(), Modern()
print(l.tag(), m.tag())
print(isinstance(l, Legacy), isinstance(l, object), isinstance(m, object))
print(isinstance(42, object), isinstance("s", object), isinstance(None, object))
print(issubclass(Legacy, object), issubclass(Modern, object))
o = object()
print(isinstance(o, object))


class Stub:
    def todo(self):
        ...

    def ready(self):
        return "done"


s = Stub()
print(s.todo(), s.ready())
print(..., repr(...), bool(...))
placeholder = ...
print(placeholder is ..., placeholder is Ellipsis, Ellipsis is ...)


def sig(a, b=...):
    if b is ...:
        return f"only {a}"
    return f"{a} and {b}"


print(sig(1), sig(1, 2), sig(1, None))
