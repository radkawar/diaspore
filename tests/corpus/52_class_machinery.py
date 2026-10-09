class Registry:
    plugins = []

    def __init_subclass__(cls, tag=None, **kw):
        Registry.plugins.append((cls.__name__, tag))


class A(Registry, tag="alpha"):
    pass


class B(Registry, tag="beta"):
    pass


print(Registry.plugins)


def factory(meta_name, n):
    return type(meta_name, (Registry,), {"n": n, "double": lambda self: self.n * 2})


Dyn = factory("Dyn", 21)
print(Dyn.__name__, Dyn(). double(), isinstance(Dyn(), Registry))


def logging_meta(name, bases, ns):
    ns["made_by"] = "logging_meta"
    return type(name, tuple(bases), ns)


class Traced(metaclass=logging_meta):
    x = 1


print(Traced.made_by, Traced.x, Traced.__name__)

try:
    class Bad(Registry.plugins.__class__ if False else object, nope=1):
        pass
except TypeError as e:
    print("kwargs without hook:", "init_subclass" in str(e) or "keyword" in str(e))
print("done")
class Base:
    registry = []
    def __init_subclass__(cls, **kw):
        Base.registry.append((cls.__name__, sorted(kw.items())))


d = {"tag": "dyn", "level": 3}
class A(Base, **d):
    pass


class B(*[Base], tag="star"):
    pass


print(Base.registry)


def meta(name, bases, ns, **kw):
    ns["meta_kw"] = sorted(kw.items())
    return type(name, bases, ns)


mk = {"metaclass": meta, "flavor": "vanilla"}
class C(**mk):
    x = 1


print(C.meta_kw, C.x, C.__name__)
