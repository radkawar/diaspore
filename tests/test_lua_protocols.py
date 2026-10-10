"""Lua builtin protocol dispatch follows CPython's type-level semantics."""

from pathlib import Path

from .conftest import assert_matches, run_cpython, run_diaspore_lua


def test_lua_reversed_protocol(tmp_path: Path, lua_vm: list[str]) -> None:
    entry = tmp_path / "reverse_protocol.py"
    entry.write_text(
        """
class Custom:
    def __reversed__(self):
        return 'custom result'
print(reversed(Custom()))
class Sequence:
    def __init__(self):
        self.values = [1, 2, 3]
    def __len__(self):
        return len(self.values)
    def __getitem__(self, index):
        return self.values[index]
source = Sequence()
iterator = reversed(source)
source.values[2] = 30
print(next(iterator))
source.values.clear()
print(list(iterator))
source.values.extend([4, 5, 6])
print(list(iterator))
items = [1, 2, 3]
iterator = reversed(items)
items[2] = 9
print(list(iterator))
class Refuses(Sequence):
    __reversed__ = None
try:
    reversed(Refuses())
except TypeError:
    print('refused')
class Fails(Sequence):
    def __getitem__(self, index):
        raise ValueError('visible')
try:
    next(reversed(Fails()))
except ValueError as error:
    print(error)
print(list(reversed({'first': 1, 'second': 2})))
""",
        encoding="utf-8",
    )
    expected = run_cpython(entry)
    assert expected.code == 0, expected.err_line
    assert_matches(run_diaspore_lua(entry, lua_vm, tmp_path), expected, "Lua reversed protocol")


def test_lua_dictionary_update_protocol(tmp_path: Path, lua_vm: list[str]) -> None:
    entry = tmp_path / "mapping_protocol.py"
    entry.write_text(
        """
class Mapping:
    def keys(self):
        return ['first', 'second']
    def __getitem__(self, key):
        return key.upper()
    def __iter__(self):
        raise AssertionError('mapping must not iterate pairs')
print(dict(Mapping()))
target = {'kept': 1}
target.update(Mapping(), last=3)
print(target)
class Subdict(dict):
    def __iter__(self):
        yield 'chosen'
    def keys(self):
        return ['chosen']
    def __getitem__(self, key):
        return 12
print(dict(Subdict(ignored=99)))
class NativeIteration(dict):
    def keys(self):
        raise AssertionError('native iterator permits direct dict copy')
    def __getitem__(self, key):
        raise AssertionError('native iterator permits direct dict copy')
print(dict(NativeIteration(stored=7)))
target = {}
target.update(NativeIteration(stored=8))
print(target)
class Pairs:
    def __iter__(self):
        yield ('first', 1)
        yield ('second', 2)
        raise ValueError('after entries')
target = {}
try:
    target.update(Pairs())
except ValueError as error:
    print(error, target)
for pairs in [[1], [('one',)], [('a', 1), ('b', 2, 3)]]:
    target = {}
    try:
        target.update(pairs)
    except (TypeError, ValueError) as error:
        print(type(error).__name__, str(error), target)
""",
        encoding="utf-8",
    )
    expected = run_cpython(entry)
    assert expected.code == 0, expected.err_line
    assert_matches(run_diaspore_lua(entry, lua_vm, tmp_path), expected, "Lua dictionary protocol")


def test_lua_native_subclass_string_protocol(tmp_path: Path, lua_vm: list[str]) -> None:
    entry = tmp_path / "subclass_strings.py"
    entry.write_text(
        """
import types
proxy = types.MappingProxyType({'value': 1})
print(str(proxy), repr(proxy))
for base in [int, float, str, bytes, list, tuple, dict]:
    class Custom(base):
        def __repr__(self):
            return 'custom'
    print(base.__name__, str(Custom()), repr(Custom()))
    class Explicit(Custom):
        def __str__(self):
            return 'explicit'
    print(str(Explicit()), repr(Explicit()))
""",
        encoding="utf-8",
    )
    expected = run_cpython(entry)
    assert expected.code == 0, expected.err_line
    assert_matches(run_diaspore_lua(entry, lua_vm, tmp_path), expected, "Lua subclass strings")


def test_lua_object_and_numeric_protocols(tmp_path: Path, lua_vm: list[str]) -> None:
    entry = tmp_path / "object_protocol.py"
    entry.write_text(
        """
class Value:
    def __repr__(self):
        return 'represented'
    def __str__(self):
        return 'displayed'
value = Value()
print(object.__repr__(value).startswith('<__main__.Value object at 0x'))
print(object.__str__(value), object.__format__(value, ''))
try:
    object.__format__(value, 'x')
except TypeError as error:
    print(error)
class Number(float):
    pass
number = Number(-8)
print(-number, +number, abs(number), format(number, '.2f'))
class Integer(int):
    pass
print(-Integer(8), +Integer(8), ~Integer(8), abs(Integer(-8)))
class Unary:
    def __neg__(self):
        return 'negative'
    def __pos__(self):
        return 'positive'
    def __invert__(self):
        return 'inverted'
print(-Unary(), +Unary(), ~Unary())
class Sequence(list):
    pass
sequence = Sequence([1, 2])
original = sequence
sequence += [3]
print(sequence is original, sequence)
sequence *= 2
print(sequence is original, sequence)
class Meta(type):
    pass
class Class(metaclass=Meta):
    pass
print(isinstance(Class, Meta), isinstance(Class, type), isinstance(int, Meta))
print(Class, repr(Class), int)
class ReprMeta(type):
    def __repr__(cls):
        return 'custom class'
class Special(metaclass=ReprMeta):
    pass
print(Special, repr(Special))
class RejectMeta(type):
    def __instancecheck__(cls, value):
        return False
    def __subclasscheck__(cls, value):
        return False
class Rejected(metaclass=RejectMeta):
    pass
class SubRejected(Rejected):
    pass
print(isinstance(Rejected(), Rejected), isinstance(SubRejected(), Rejected))
print(issubclass(Rejected, Rejected), issubclass(SubRejected, Rejected))
print(isinstance(Class(), (Rejected, Class)), issubclass(Class, (Rejected, Class)))
""",
        encoding="utf-8",
    )
    expected = run_cpython(entry)
    assert expected.code == 0, expected.err_line
    assert_matches(run_diaspore_lua(entry, lua_vm, tmp_path), expected, "Lua object protocols")


def test_lua_metaclass_representation(tmp_path: Path, lua_vm: list[str]) -> None:
    entry = tmp_path / "metaclass_repr.py"
    entry.write_text(
        """
class Meta(type):
    pass
class Plain(metaclass=Meta):
    pass
print(Plain, repr(Plain), type.__repr__(Plain))
print(Meta.__repr__ is type.__repr__)
print(type.__dict__['__repr__'] is type.__repr__)
class CustomMeta(type):
    def __repr__(cls):
        return 'custom representation'
class InheritedMeta(CustomMeta):
    pass
class Custom(metaclass=InheritedMeta):
    pass
print(Custom, repr(Custom), type.__repr__(Custom))
class DelegatingMeta(type):
    def __repr__(cls):
        return 'wrapped ' + super().__repr__()
class Delegating(metaclass=DelegatingMeta):
    pass
print(Delegating, repr(Delegating), type.__repr__(Delegating))
class ObjectMeta(type):
    __repr__ = object.__repr__
class ObjectStyle(metaclass=ObjectMeta):
    pass
print(repr(ObjectStyle).startswith('<__main__.ObjectMeta object at 0x'))
print(type.__repr__(ObjectStyle))
Plain.__module__ = 'elsewhere'
Plain.__qualname__ = 'Renamed'
print(Plain, type.__repr__(Plain), type.__repr__(int))
for operation in (
    lambda: type.__repr__(),
    lambda: type.__repr__(1),
    lambda: type.__repr__(int, int),
    lambda: type.__repr__(int, x=1),
):
    try:
        operation()
    except TypeError as error:
        print(error)
""",
        encoding="utf-8",
    )
    expected = run_cpython(entry)
    assert expected.code == 0, expected.err_line
    assert_matches(run_diaspore_lua(entry, lua_vm, tmp_path), expected, "Lua metaclass repr")


def test_lua_dynamic_type_keywords_and_namespace(tmp_path: Path, lua_vm: list[str]) -> None:
    entry = tmp_path / "dynamic_type.py"
    entry.write_text(
        """
events = []
class Meta(type):
    def __new__(meta, name, bases, namespace, *, flag=None):
        events.append(('new', name, flag, namespace is original))
        return super().__new__(meta, name, bases, namespace)
    def __init__(cls, name, bases, namespace, *, flag=None):
        events.append(('init', name, flag, namespace is original))
original = {}
Base = Meta('Base', (), original)
events.clear()
class Namespace(dict):
    pass
original = Namespace(value=42)
Derived = type('Derived', (Base,), original, flag='forwarded')
print(type(Derived) is Meta, Derived.value)
print(events)
class Plain:
    def __init_subclass__(cls, *, marker, **kwargs):
        super().__init_subclass__(**kwargs)
        cls.marker = marker
Child = type('Child', (Plain,), {}, marker='hook')
print(Child.marker)
""",
        encoding="utf-8",
    )
    expected = run_cpython(entry)
    assert expected.code == 0, expected.err_line
    assert_matches(run_diaspore_lua(entry, lua_vm, tmp_path), expected, "Lua dynamic type")


def test_lua_native_callback_recursion_and_return_paths(
    tmp_path: Path, lua_vm: list[str]
) -> None:
    entry = tmp_path / "native_callback_recursion.py"
    entry.write_text(
        """
import sys
from types import FunctionType

class Getter:
    def __getattribute__(self, name):
        remaining = object.__getattribute__(self, 'remaining')
        if remaining:
            object.__setattr__(self, 'remaining', remaining - 1)
            return getattr(self, name)
        return name

value = Getter()
object.__setattr__(value, 'remaining', 300)
assert getattr(value, 'answer') == 'answer'

class Missing:
    def __getattr__(self, name):
        if self.remaining:
            self.remaining -= 1
            if hasattr(self, name):
                return 1
        raise AttributeError(name)

value = Missing()
value.remaining = 300
assert not hasattr(value, 'missing')

class RecursiveRepr:
    def __repr__(self):
        if self.remaining:
            self.remaining -= 1
            return repr([self])
        return 'done'

value = RecursiveRepr()
value.remaining = 250
assert repr(value) == '[' * 250 + 'done' + ']' * 250
print('recursive native getattr hasattr and repr callbacks passed')

def bare():
    return
def identity(value):
    return value
def keyword(a, /, b=2, *, c=3):
    return (a, b, c)
def outer(captured):
    def inner(value):
        return captured + value
    return inner
def current_frame():
    return sys._getframe()
def multiple(value):
    result = value
    return result

assert bare() is None
assert identity(False) is False
assert identity(None) is None
assert keyword(1, c=4) == (1, 2, 4)
assert outer(40)(2) == 42
frame = current_frame()
assert frame.f_code.co_name == 'current_frame'
assert frame.f_locals == {}
assert frame.f_back is sys._getframe()
assert FunctionType(compile('False', '<expression>', 'eval'), {})() is False
identity.__code__ = multiple.__code__
assert identity(False) is False
print('direct and general return paths passed')
""",
        encoding="utf-8",
    )
    expected = run_cpython(entry)
    assert expected.code == 0, expected.err_line
    assert_matches(run_diaspore_lua(entry, lua_vm, tmp_path), expected, "Lua native callbacks")


def test_lua_activation_reuse_preserves_escaped_state(
    tmp_path: Path, lua_vm: list[str]
) -> None:
    entry = tmp_path / "activation_reuse.py"
    entry.write_text(
        """
import sys
import types

# Values exposed by snapshots and lexical cells outlive repeated activations.
def snapshot(value):
    return locals()
a = snapshot(11)
b = snapshot(22)
assert a['value'] == 11 and b['value'] == 22

def counter(value):
    def advance():
        nonlocal value
        value += 1
        return value
    return advance
first, second = counter(10), counter(20)
assert (first(), second(), first()) == (11, 21, 12)

def child(value):
    captured = lambda: value
    return sys._getframe(), captured

def parent(value):
    return child(value)
old_frame, old_closure = parent(11)
new_frame, new_closure = parent(22)
assert old_frame is not new_frame
assert old_frame.f_locals['value'] == 11
assert old_frame.f_back.f_locals['value'] == 11
assert new_frame.f_back.f_locals['value'] == 22
assert old_closure() == 11 and new_closure() == 22

def generator():
    yield sys._getframe()
g = generator()
paused = next(g)
assert paused.f_back is None
def close_generator(marker):
    g.close()
close_generator('original')
close_generator('later')
assert paused.f_back.f_locals['marker'] == 'original'

def optional(a=8):
    return a
assert optional(3) == 3
try:
    optional(1, a=2)
except TypeError:
    pass
else:
    raise AssertionError('duplicate binding accepted')
assert optional() == 8

def conditional(flag):
    if flag:
        value = 9
    return value
assert conditional(True) == 9
try:
    conditional(False)
except UnboundLocalError:
    pass
else:
    raise AssertionError('stale local survived reuse')

def packed(*args):
    return args
one, two = packed(1, 2), packed(3, 4)
assert one == (1, 2) and two == (3, 4)

def keywords(a, /, **kw):
    return a, kw
one, two = keywords(1, a=2), keywords(3, a=4)
assert one == (1, {'a': 2}) and two == (3, {'a': 4})

def target(a):
    return a
assert target(9) == 9
def replacement(a, b):
    return a + b
def change_code():
    target.__code__ = replacement.__code__
    return 1
try:
    target(change_code())
except TypeError:
    pass
else:
    raise AssertionError('old signature used after argument evaluation')
assert target(2, 3) == 5

def active_replacement(value):
    return value * 2
def active_swap(value):
    active_swap.__code__ = active_replacement.__code__
    return value
assert active_swap(11) == 11
assert active_swap(12) == 24

order = []
def identity(value):
    order.append(value)
    return value
def combine(a, b, c):
    return a, b, c
assert combine(identity(1), identity(2), identity(3)) == (1, 2, 3)
assert order == [1, 2, 3]

class Base:
    def value(self):
        return 9
class Derived(Base):
    def value(self):
        return super().value() + 1
instance = Derived()
assert [instance.value() for _ in range(3)] == [10, 10, 10]
assert types.FunctionType(compile('False', '<eval>', 'eval'), {})() is False
assert types.FunctionType(compile('None', '<eval>', 'eval'), {})() is None
print('activation reuse preserves escaped state and binding semantics')
""",
        encoding="utf-8",
    )
    expected = run_cpython(entry)
    assert expected.code == 0, expected.err_line
    assert_matches(run_diaspore_lua(entry, lua_vm, tmp_path), expected, "Lua activation reuse")
