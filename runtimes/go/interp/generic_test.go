package interp_test

import (
	"bytes"
	"testing"

	"github.com/radkawar/diaspore/runtimes/go/dspc"
	"github.com/radkawar/diaspore/runtimes/go/interp"
)

func TestRuntimeGenericAliases(t *testing.T) {
	cases := []struct {
		name   string
		source string
	}{
		{"native_documentation", `import types
for singleton in (None, NotImplemented, Ellipsis):
    assert singleton.__doc__ is None
    assert type(singleton).__doc__ is None
    assert type(singleton).__dict__['__doc__'] is None
for value in (0, 0.0, True, '', b'', bytearray(), memoryview(b''), [], (), {}, set(),
              frozenset(), range(0), slice(0), 0j, object()):
    assert value.__doc__ == type(value).__doc__
    assert type(value).__dict__['__doc__'] == type(value).__doc__
assert float.__doc__ == 'Convert a string or number to a floating-point number, if possible.'
assert list.__doc__.startswith('Built-in mutable sequence.')
class User:
    pass
assert User.__doc__ is None and User().__doc__ is None
def getter(self):
    'Actual getter documentation.'
    return 1
assert getter.__doc__ == 'Actual getter documentation.'
assert property(getter).__doc__ == getter.__doc__
assert types.DynamicClassAttribute().__doc__ is None
assert '__doc__' not in getter.__dict__
assert getter.__code__.co_consts[0] == 'Actual getter documentation.'
rebuilt = types.FunctionType(getter.__code__, globals())
assert rebuilt.__doc__ == getter.__doc__
descriptor = types.FunctionType.__dict__['__doc__']
assert descriptor.__get__(getter) == getter.__doc__
getter.__dict__['__doc__'] = 'dictionary entry'
assert getter.__doc__ == 'Actual getter documentation.'
getter.__doc__ = ['arbitrary object']
assert getter.__doc__ == ['arbitrary object']
assert getter.__dict__['__doc__'] == 'dictionary entry'
del getter.__doc__
assert getter.__doc__ is None
descriptor.__set__(getter, 'changed')
assert getter.__doc__ == 'changed'
descriptor.__delete__(getter)
assert getter.__doc__ is None
seen_docs = []
def decorate(function):
    seen_docs.append(function.__doc__)
    return function
@decorate
def empty_doc():
    ''
    return 2
@decorate
def generator():
    'Generator documentation.'
    yield 3
@decorate
async def coroutine():
    'Coroutine documentation.'
    return 4
assert seen_docs == ['', 'Generator documentation.', 'Coroutine documentation.']
assert empty_doc() == 2 and list(generator()) == [3]
getter.__doc__ = 'independent'
getter.__code__ = empty_doc.__code__
assert getter.__doc__ == 'independent'
assert types.FunctionType(empty_doc.__code__, globals()).__doc__ == ''
`},
		{"dynamic_metaclass_selection", `events = []
class MetaMeta(type):
    def __call__(cls, *args, **kwargs):
        raise AssertionError('type() invoked metaclass __call__')
class Meta(type, metaclass=MetaMeta):
    @classmethod
    def __prepare__(mcls, name, bases, **kwargs):
        raise AssertionError('type() invoked __prepare__')
    def __new__(mcls, name, bases, namespace, *, marker=None):
        events.append(('new', name, marker))
        return type.__new__(mcls, name, bases, namespace)
    def __init__(cls, name, bases, namespace, *, marker=None):
        events.append(('init', name, marker))
Base = type.__new__(Meta, 'Base', (), {})
assert events == []
Derived = type('Derived', (Base,), {'data': 3}, marker=7)
assert type(Derived) is Meta and Derived.data == 3
assert events == [('new', 'Derived', 7), ('init', 'Derived', 7)]
Raw = type.__new__(type, 'Raw', (Base,), {}, marker=8)
assert type(Raw) is Meta
assert events == [('new', 'Derived', 7), ('init', 'Derived', 7), ('new', 'Raw', 8)]
class OtherMeta(type):
    pass
Other = type.__new__(OtherMeta, 'Other', (), {})
try:
    type('Conflict', (Base, Other), {})
except TypeError:
    pass
else:
    raise AssertionError('metaclass conflict accepted')
`},
		{"function_descriptor", `import types
def function(receiver, value):
    return receiver, value
owner = object()
descriptor = types.FunctionType.__get__
assert descriptor is types.FunctionType.__dict__['__get__']
assert type(descriptor) is types.WrapperDescriptorType
assert descriptor.__objclass__ is types.FunctionType
assert function.__get__.__self__ is function
assert function.__get__(None, object) is function
assert descriptor(function, None, object) is function
bound = function.__get__(owner)
assert type(bound) is types.MethodType
assert bound.__self__ is owner and bound.__func__ is function
assert bound(7) == (owner, 7)
assert descriptor(function, owner, object)(8) == (owner, 8)
assert descriptor.__get__(function, types.FunctionType)(owner)(9) == (owner, 9)
for args in [(function, None), (function, None, None), (object(), owner)]:
    try:
        descriptor(*args)
    except TypeError:
        pass
    else:
        raise AssertionError('invalid function descriptor call accepted')
class Owner:
    method = function
instance = Owner()
assert Owner.method is function
assert instance.method.__func__ is function and instance.method.__self__ is instance
function.__get__ = 'shadowed'
assert function.__get__ == 'shadowed'
assert instance.method(3) == (instance, 3)
del function.__get__
assert function.__get__(None, Owner) is function
`},
		{"metaclass_instances", `assert type(None).__new__ is None.__new__
assert int.__dict__['__new__'] is int.__new__
assert (1).__new__ is int.__new__
assert None.__new__(type(None)) is None
try:
    None.__new__(type(None), 1)
except TypeError:
    pass
else:
    raise AssertionError('NoneType constructor accepted arguments')
class Meta(type):
    pass
class DerivedMeta(Meta):
    pass
class First(metaclass=Meta):
    pass
class Second(metaclass=DerivedMeta):
    pass
assert isinstance(First, Meta)
assert isinstance(Second, Meta)
assert isinstance(Second, DerivedMeta)
assert not isinstance(First, DerivedMeta)
assert not isinstance(First(), Meta)
assert not isinstance(type, Meta)
class CheckMeta(type):
    def __instancecheck__(cls, instance):
        return False
class CheckedMeta(type, metaclass=CheckMeta):
    pass
class CheckedChild(CheckedMeta):
    pass
class Exact(metaclass=CheckedMeta):
    pass
class Derived(metaclass=CheckedChild):
    pass
assert isinstance(Exact, CheckedMeta)
assert not isinstance(Derived, CheckedMeta)
`},
		{"equality_hash_and_union", `import types

first = list[int]
second = list[int]
assert first == second and first is not second
assert first != list[str]
assert hash(first) == hash(second) == hash(list) ^ hash((int,))
assert {first: 'value'}[second] == 'value'
assert len({first, second, list[str]}) == 2
assert first.__eq__(42) is NotImplemented
assert first.__or__(42) is NotImplemented
assert int | int is int
assert list[int] | list[int] == list[int]
assert (int | str) | int == str | int
assert (int | str | None).__args__ == (int, str, type(None))
assert int | None == None | int == type(None) | int
assert (int | str) != (int | bytes)
assert hash(int | str) == hash(str | int) == hash(frozenset((int, str)))
assert {int | str: 4}[str | int] == 4
assert (list[int] | str).__args__ == (list[int], str)
assert list[int] | str == str | list[int]
assert (list[int] | str | list[int]).__args__ == (list[int], str)
assert len({list[int] | str, str | list[int]}) == 1
try:
    None | None
except TypeError:
    pass
else:
    raise AssertionError('None is not an operator-bearing type')
for value in (list[[]], list[[]] | int):
    try:
        hash(value)
    except TypeError:
        pass
    else:
        raise AssertionError('unhashable alias argument accepted')
try:
    (list[[]] | int) == (int | list[[]])
except TypeError:
    pass
else:
    raise AssertionError('union equality must enforce set hashability')

class Alias(types.GenericAlias):
    pass
assert Alias(list, (int,)) == Alias(list, (int,)) == first
assert hash(Alias(list, (int,))) == hash(first)

class Meta(type):
    def __or__(cls, other):
        return 'forward'
    def __ror__(cls, other):
        return 'reverse'
class Custom(metaclass=Meta):
    pass
assert Custom | int == 'forward'
assert int | Custom == 'reverse'

class Decline(type):
    def __or__(cls, other):
        return NotImplemented
    def __ror__(cls, other):
        return NotImplemented
class Left(metaclass=Decline):
    pass
class Right(metaclass=Decline):
    pass
try:
    Left | Right
except TypeError:
    pass
else:
    raise AssertionError('declined custom union operators were ignored')
`},
		{"substitution_protocol", `import types

class Parameter:
    def __typing_subst__(self, value):
        return value
T = Parameter()
S = Parameter()
alias = dict[T, list[S]]
assert alias.__parameters__ == (T, S)
assert alias.__parameters__ is alias.__parameters__
assert alias[int, str] == dict[int, list[str]]
assert tuple[T, T].__parameters__ == (T,)
assert tuple[T, T][str] == tuple[str, str]
assert (list[T] | int).__parameters__ == (T,)
assert (list[T] | int)[str] == list[str] | int
for value, args in ((alias, int), (alias, (int, str, bytes)), (list[int], str)):
    try:
        value[args]
    except TypeError:
        pass
    else:
        raise AssertionError('invalid generic arity accepted')

class BoundParameter(Parameter):
    def __typing_subst__(self, value):
        if value is not int:
            raise ValueError('invalid parameter')
        return value
try:
    list[BoundParameter()][str]
except ValueError as error:
    assert str(error) == 'invalid parameter'
else:
    raise AssertionError('substitution exception lost')

class BrokenParameter:
    @property
    def __typing_subst__(self):
        raise RuntimeError('parameter lookup failed')
try:
    list[BrokenParameter()].__parameters__
except RuntimeError as error:
    assert str(error) == 'parameter lookup failed'
else:
    raise AssertionError('parameter property exception lost')

class Ordinary:
    @property
    def __parameters__(self):
        raise AssertionError('class descriptor must not be interpreted')
assert list[Ordinary].__parameters__ == ()

unpacked, = tuple[int, str]
assert unpacked.__unpacked__
assert unpacked.__typing_unpacked_tuple_args__ == (int, str)
assert tuple[int, str].__typing_unpacked_tuple_args__ is None
assert unpacked != tuple[int, str]
assert hash(unpacked) == hash(tuple[int, str])
assert repr(unpacked) == '*tuple[int, str]'
assert repr(tuple[()]) == 'tuple[()]'
assert alias[unpacked] == dict[int, list[str]]
starred, = tuple[T]
assert starred[int].__unpacked__
assert starred[int].__args__ == (int,)

class Box:
    pass
boxed = types.GenericAlias(Box, int)
assert boxed().__orig_class__ is boxed
`},
		{"typing_provider_parameters", `import builtins
from typing import ParamSpec, TypeAliasType, TypeVar, TypeVarTuple, Unpack

T = TypeVar('T')
S = TypeVar('S')
assert dict[T, list[S]][int, str] == dict[int, list[str]]
assert (list[T] | int)[str] == list[str] | int
P = ParamSpec('P')
assert list[P][int, str].__args__ == ((int, str),)
Ts = TypeVarTuple('Ts')
variadic = tuple[Unpack[Ts]]
assert variadic.__parameters__ == (Ts,)
assert variadic[int, str] == tuple[int, str]
assert variadic[()] == tuple[()]
assert dict[int, variadic][str, bytes] == dict[int, tuple[str, bytes]]
Named = TypeAliasType('Named', int)
assert Named | str == str | Named
assert (Named | str | Named).__args__ == (Named, str)
assert hash(Named | str) == hash(str | Named)
assert builtins._typing_union(object(), int) is NotImplemented
`},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			compiled, err := dspc.CompileSource("/src/main.py", tc.source, dspc.CompileOptions{})
			if err != nil || compiled.Code != 0 {
				t.Fatalf("compile: %v %s", err, compiled.Stderr)
			}
			bundle, err := interp.LoadBundle([]byte(compiled.Bundle))
			if err != nil {
				t.Fatal(err)
			}
			var stderr bytes.Buffer
			vm := interp.New(bundle, interp.WithStderr(&stderr), interp.WithSnippetCompiler(dspc.CompileSnippet))
			defer vm.Close()
			if code := vm.Run(); code != 0 {
				t.Fatalf("Run() = %d: %s", code, stderr.String())
			}
		})
	}
}
