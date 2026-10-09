events = []


class Frozen:
    def __init__(self, value):
        object.__setattr__(self, "value", value)

    def __setattr__(self, name, value):
        events.append(("set", name, value))
        raise AttributeError("frozen")

    def __delattr__(self, name):
        events.append(("del", name))
        raise AttributeError("frozen")


class Child(Frozen):
    pass


obj = Child(12)
print(obj.value)
try:
    obj.value = 20
except AttributeError as exc:
    print(str(exc))
try:
    del obj.value
except AttributeError as exc:
    print(str(exc))
try:
    setattr(obj, "extra", 3)
except AttributeError:
    print("setattr frozen")
try:
    delattr(obj, "value")
except AttributeError:
    print("delattr frozen")
print(events)
object.__delattr__(obj, "value")
print(hasattr(obj, "value"))


class Managed:
    def __setattr__(self, name, value):
        super().__setattr__(name, value + 1)

    def __delattr__(self, name):
        super().__delattr__(name)

    @property
    def value(self):
        return self._value

    @value.setter
    def value(self, value):
        object.__setattr__(self, "_value", value * 2)

    @value.deleter
    def value(self):
        object.__delattr__(self, "_value")


managed = Managed()
managed.value = 4
print(managed.value)
object.__setattr__(managed, "value", 7)
print(managed.value)
del managed.value
print(hasattr(managed, "_value"))
managed.x = 8
print(managed.x)
del managed.x
print(hasattr(managed, "x"))


class Plain:
    marker = 1


def method(self, number):
    return self.marker + number


setattr(Plain, "method", method)
plain = Plain()
print(plain.method(4), Plain.method(plain, 5))
print(Plain.__dict__["marker"], Plain.__dict__["method"] is method)
print(Plain.__bases__ == (object,), Child.__bases__ == (Frozen,))
print(object.__bases__ == ())
namespace = Plain.__dict__
setattr(Plain, "later", 7)
print(namespace["later"])
try:
    namespace["marker"] = 10
except TypeError:
    print("read-only namespace")
delattr(Plain, "marker")
print("marker" in Plain.__dict__)
try:
    delattr(Child, "__init__")
except AttributeError:
    print("cannot delete inherited attribute")
del Plain.method
print(hasattr(Plain, "method"))
