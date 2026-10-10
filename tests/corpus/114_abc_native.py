"""Abstract class initialization, registry invalidation, and subclass hooks."""

import _abc
from abc import ABC, ABCMeta, abstractmethod, get_cache_token


class Shape(ABC):
    @abstractmethod
    def area(self):
        pass


print(Shape.__abstractmethods__ == frozenset({"area"}))
try:
    Shape()
except TypeError:
    print("abstract")


class Square(Shape):
    def area(self):
        return 4


print(Square.__abstractmethods__ == frozenset(), Square().area())


class Virtual:
    pass


class Child(Virtual):
    pass


print(issubclass(Virtual, Shape), isinstance(Virtual(), Shape))
before = get_cache_token()
print(Shape.register(Virtual) is Virtual, get_cache_token() == before + 1)
print(issubclass(Virtual, Shape), isinstance(Virtual(), Shape))
print(issubclass(Child, Shape), isinstance(Child(), Shape))
registry, positive, negative, version = _abc._get_dump(Shape)
print({ref() for ref in registry} == {Virtual})
print(Virtual not in {ref() for ref in positive}, Child in {ref() for ref in positive})
print(Virtual not in {ref() for ref in negative}, version == get_cache_token())
before = get_cache_token()
Shape.register(Virtual)
print(get_cache_token() == before)
_abc._reset_registry(Shape)
_abc._reset_caches(Shape)
print(issubclass(Virtual, Shape), issubclass(Child, Shape))


class Left(ABC):
    pass


class Right(ABC):
    pass


Left.register(Right)
try:
    Right.register(Left)
except RuntimeError as error:
    print(str(error))
try:
    Left.register(1)
except TypeError as error:
    print(str(error))


class Hooked(ABC):
    @classmethod
    def __subclasshook__(cls, candidate):
        if "marker" in candidate.__dict__:
            return True
        return NotImplemented


class Marked:
    marker = 1


print(issubclass(Marked, Hooked), isinstance(Marked(), Hooked))


class BadHook(ABC):
    @classmethod
    def __subclasshook__(cls, candidate):
        return "invalid"


try:
    issubclass(Marked, BadHook)
except AssertionError as error:
    print(str(error))


class CustomMeta(ABCMeta):
    def __subclasscheck__(cls, candidate):
        return candidate is Marked


class Custom(metaclass=CustomMeta):
    pass


print(isinstance(Marked(), Custom), isinstance(Virtual(), Custom))


class Disguised:
    @property
    def __class__(self):
        return Marked


print(isinstance(Disguised(), Custom))
