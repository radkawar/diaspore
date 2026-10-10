import sys
import types
from abc import ABC, abstractmethod


def make_reader():
    value = 7

    def read_value():
        return value

    return read_value


reader = make_reader()
print(type(reader) is types.FunctionType)
print(type(reader.__code__) is types.CodeType)
print(reader.__code__.co_name, reader.__code__.co_argcount)
print(reader.__code__.co_freevars)
cell = reader.__closure__[0]
print(type(cell) is types.CellType, cell.cell_contents)
cell.cell_contents = 9
print(reader())
assert reader.__closure__ is reader.__closure__
assert reader.__dict__ == {}
clone = types.FunctionType(reader.__code__, globals(), closure=reader.__closure__)
assert clone.__closure__ is reader.__closure__
assert clone() == 9


def make_multiplier():
    other_value = 3

    def multiply():
        return other_value * 2

    return multiply


clone.__code__ = make_multiplier().__code__
assert clone() == 18
assert clone.__code__.co_freevars == ('other_value',)

print(type(type.__dict__) is types.MappingProxyType)
print(type(sys.implementation) is types.SimpleNamespace)
print(sys.modules['types'] is types)
print(sys.maxunicode == 1114111, sys.byteorder in ('little', 'big'))
print(sys.intern('portable') is sys.intern('portable'))
print(type(object.__init__) is types.WrapperDescriptorType)
print(type(str.join) is types.MethodDescriptorType)
print(type(dict.__dict__['fromkeys']) is types.ClassMethodDescriptorType)

try:
    raise ValueError('metadata')
except ValueError as error:
    print(type(error.__traceback__) is types.TracebackType)
    print(type(error.__traceback__.tb_frame) is types.FrameType)
    print(sys.exception() is error, sys.exc_info()[1] is error)


class Interface(ABC):
    @abstractmethod
    def value(self):
        pass


class Implementation(Interface):
    def value(self):
        return 12


print(isinstance(Implementation(), Interface), issubclass(Implementation, Interface))
try:
    Interface()
except TypeError:
    print('abstract')


class Unrelated:
    pass


Interface.register(Unrelated)
print(isinstance(Unrelated(), Interface), issubclass(Unrelated, Interface))
print(Implementation.__bases__ == (Interface,))
print(Implementation.__mro__[1] is Interface)
print('value' in Implementation.__dict__)
alias = list[int]
print(type(alias) is types.GenericAlias, alias.__origin__ is list, alias.__args__ == (int,))
print(type(int | str) is types.UnionType)


class Values(list[int]):
    pass


assert Values.__bases__ == (list,)
assert Values.__orig_bases__[0].__origin__ is list
assert Values((1, 2)) == [1, 2]


class DocumentedProperty(property):
    pass


class PropertyOwner:
    @DocumentedProperty
    def value(self):
        """The current value."""
        return 11


assert PropertyOwner().value == 11
assert PropertyOwner.value.__doc__ == 'The current value.'
replacement = PropertyOwner.value.getter(lambda self: 13)
assert type(replacement) is DocumentedProperty
assert replacement.__get__(PropertyOwner(), PropertyOwner) == 13
