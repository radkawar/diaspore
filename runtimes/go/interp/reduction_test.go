package interp_test

import (
	"bytes"
	"testing"

	"github.com/radkawar/diaspore/runtimes/go/dspc"
	"github.com/radkawar/diaspore/runtimes/go/interp"
)

func TestObjectReductionProtocols(t *testing.T) {
	cases := []struct{ name, source string }{
		{"dictionary_and_legacy", `import copyreg
class Plain:
    pass
value = Plain()
assert object.__getstate__(value) is None
for protocol in (0, 1):
    reduced = object.__reduce_ex__(value, protocol)
    assert reduced == (copyreg._reconstructor, (Plain, object, None))
value.answer = 42
assert object.__getstate__(value) is value.__dict__
for protocol in (0, 1):
    reduced = object.__reduce_ex__(value, protocol)
    assert reduced[0] is copyreg._reconstructor
    assert reduced[1] == (Plain, object, None)
    assert reduced[2] is value.__dict__
for protocol in (2, 3, 4, 5):
    reduced = object.__reduce_ex__(value, protocol)
    assert reduced[0] is copyreg.__newobj__
    assert reduced[1] == (Plain,)
    assert reduced[2] is value.__dict__
    assert reduced[3:] == (None, None)
    restored = reduced[0](*reduced[1])
    restored.__dict__.update(reduced[2])
    assert type(restored) is Plain and restored.answer == 42
class Override:
    def __reduce__(self):
        return 'custom-reduction'
value = Override()
assert object.__reduce_ex__(value, 4) == 'custom-reduction'
assert object.__reduce__(value)[0] is copyreg._reconstructor
class Inherited(Override):
    pass
assert object.__reduce_ex__(Inherited(), 2) == 'custom-reduction'
class CustomState:
    def __getstate__(self):
        return ('custom', 9)
value = CustomState()
assert object.__reduce_ex__(value, 2)[2] == ('custom', 9)
assert object.__getstate__(value) is None
`},
		{"constructor_arguments", `import copyreg
class Positional:
    def __getnewargs__(self):
        return ('argument', 42)
value = Positional()
reduced = object.__reduce_ex__(value, 2)
assert reduced[0] is copyreg.__newobj__
assert reduced[1] == (Positional, 'argument', 42)
class Keywords:
    def __getnewargs_ex__(self):
        return ((7,), {'answer': 42})
    def __getnewargs__(self):
        raise AssertionError('legacy callback ran')
value = Keywords()
for protocol in (2, 4, 5):
    reduced = object.__reduce_ex__(value, protocol)
    assert reduced[0] is copyreg.__newobj_ex__
    assert reduced[1] == (Keywords, (7,), {'answer': 42})
class EmptyKeywords:
    def __getnewargs_ex__(self):
        return ((), {})
assert object.__reduce_ex__(EmptyKeywords(), 2)[:2] == (copyreg.__newobj__, (EmptyKeywords,))
class InstanceOnly:
    pass
value = InstanceOnly()
value.__getnewargs__ = lambda: ('not a special method',)
assert object.__reduce_ex__(value, 2)[1] == (InstanceOnly,)
class Bad:
    def __getnewargs_ex__(self):
        return self.result
value = Bad()
for result, kind, text in (
    ([], TypeError, "__getnewargs_ex__ should return a tuple, not 'list'"),
    ((), ValueError, '__getnewargs_ex__ should return a tuple of length 2, not 0'),
    (([], {}), TypeError, "first item of the tuple returned by __getnewargs_ex__ must be a tuple, not 'list'"),
    (((), []), TypeError, "second item of the tuple returned by __getnewargs_ex__ must be a dict, not 'list'"),
):
    value.result = result
    try:
        object.__reduce_ex__(value, 2)
    except kind as error:
        assert str(error) == text
    else:
        raise AssertionError('bad constructor arguments accepted')
class BadLegacy:
    def __getnewargs__(self):
        return []
try:
    object.__reduce_ex__(BadLegacy(), 2)
except TypeError as error:
    assert str(error) == "__getnewargs__ should return a tuple, not 'list'"
else:
    raise AssertionError('bad legacy constructor arguments accepted')
`},
		{"list_dict_and_immutable_payloads", `import copyreg
class Values(list):
    def __iter__(self):
        return iter((8, 9))
values = Values([1, 2])
values.label = 'state'
reduced = object.__reduce_ex__(values, 4)
assert reduced[:2] == (copyreg.__newobj__, (Values,))
assert reduced[2] is values.__dict__
assert list(reduced[3]) == [8, 9] and reduced[4] is None
class Mapping(dict):
    def items(self):
        return [('custom', 42)]
value = Mapping(original=1)
reduced = object.__reduce_ex__(value, 4)
assert reduced[:2] == (copyreg.__newobj__, (Mapping,))
assert reduced[3] is None and list(reduced[4]) == [('custom', 42)]
for value, args in ((True, (1,)), (123, (123,)), (1.5, (1.5,)), (2+3j, (2.0, 3.0)), ('abc', ('abc',)), (b'abc', (b'abc',)), ((1, 2), ((1, 2),))):
    reduced = object.__reduce_ex__(value, 4)
    assert reduced[0] is copyreg.__newobj__
    assert reduced[1] == (type(value),) + args
    assert reduced[2:] == (None, None, None)
class Integer(int):
    pass
value = Integer(123)
value.note = 'integer state'
reduced = object.__reduce_ex__(value, 4)
assert reduced[1] == (Integer, 123) and reduced[2] is value.__dict__
assert object.__reduce_ex__(Ellipsis, 2) == 'Ellipsis'
assert object.__reduce_ex__(NotImplemented, 2) == 'NotImplemented'
assert Ellipsis.__reduce__() == 'Ellipsis'
assert NotImplemented.__reduce__() == 'NotImplemented'
assert type(Ellipsis)() is Ellipsis
assert isinstance(Ellipsis, type(Ellipsis))
for value in (set([1, 2]), frozenset([1, 2])):
    reduced = object.__reduce_ex__(value, 4)
    assert reduced[0] is type(value)
    assert set(reduced[1][0]) == {1, 2} and reduced[2] is None
class CustomSet(set):
    def __iter__(self):
        return iter([9])
assert object.__reduce_ex__(CustomSet([1]), 4)[1] == ([9],)
assert object.__reduce_ex__(bytearray(), 4) == (bytearray, (), None)
assert object.__reduce_ex__(bytearray(b'\xff'), 4) == (bytearray, ('\xff', 'latin-1'), None)
data = bytearray(b'abc')
assert data.__reduce_ex__()[1] == ('abc', 'latin-1')
reduced = data.__reduce_ex__(4)
assert reduced == (bytearray, (b'abc',), None)
data[0] = 120
assert reduced[1] == (b'abc',)
for value in (lambda: None, memoryview(b'abc'), property()):
    try:
        object.__reduce_ex__(value, 4)
    except TypeError as error:
        assert str(error).startswith('cannot pickle ')
    else:
        raise AssertionError('opaque native object reduced as empty object')
`},
		{"slots_and_state_aliasing", `import copyreg
class Slotted:
    __slots__ = ('value', '__private')
    def __init__(self):
        self.value = 1
        self.__private = 2
value = Slotted()
assert object.__getstate__(value) == (None, {'value': 1, '_Slotted__private': 2})
reduced = object.__reduce_ex__(value, 2)
assert reduced[2] == object.__getstate__(value)
class Mixed(Slotted):
    pass
value = Mixed()
value.extra = 3
state = object.__getstate__(value)
assert state == ({'extra': 3}, {'value': 1, '_Slotted__private': 2})
assert state[0] is value.__dict__
class Empty:
    __slots__ = ()
assert object.__getstate__(Empty()) is None
for protocol in (0, 1):
    try:
        object.__reduce_ex__(Slotted(), protocol)
    except TypeError as error:
        assert str(error) == 'a class that defines __slots__ without defining __getstate__ cannot be pickled'
    else:
        raise AssertionError('legacy slot reduction without explicit state accepted')
class CustomSlots(Slotted):
    def __getstate__(self):
        return {'encoded': self.value}
assert object.__reduce_ex__(CustomSlots(), 0)[2] == {'encoded': 1}
class InvalidCache:
    __slotnames__ = 1
try:
    object.__getstate__(InvalidCache())
except TypeError as error:
    assert str(error) == 'InvalidCache.__slotnames__ should be a list or None, not int'
else:
    raise AssertionError('invalid slot cache accepted')
`},
		{"protocol_and_callback_errors", `import copyreg
class Plain:
    pass
value = Plain()
class Index:
    def __index__(self):
        return 2
assert object.__reduce_ex__(value, Index())[0] is copyreg.__newobj__
for protocol in (1 << 40, -(1 << 40)):
    try:
        object.__reduce_ex__(value, protocol)
    except OverflowError as error:
        assert str(error) == 'Python int too large to convert to C int'
    else:
        raise AssertionError('protocol C-int overflow accepted')
try:
    object.__reduce_ex__(value, 2.5)
except TypeError:
    pass
else:
    raise AssertionError('non-index protocol accepted')
class Broken:
    def __getstate__(self):
        raise RuntimeError('state failure')
try:
    object.__reduce_ex__(Broken(), 2)
except RuntimeError as error:
    assert str(error) == 'state failure'
else:
    raise AssertionError('state exception lost')
other = Plain()
other.answer = 42
value.__getstate__ = other.__getstate__
assert object.__reduce_ex__(value, 2)[2] is other.__dict__
class HiddenState:
    def __getattribute__(self, name):
        if name == '__getstate__':
            raise AttributeError('hidden state')
        return object.__getattribute__(self, name)
try:
    object.__reduce_ex__(HiddenState(), 2)
except AttributeError as error:
    assert str(error) == 'hidden state'
else:
    raise AssertionError('hidden state method used an invented fallback')
`},
	}
	for _, test := range cases {
		t.Run(test.name, func(t *testing.T) {
			compiled, err := dspc.CompileSource("/src/main.py", test.source, dspc.CompileOptions{})
			if err != nil || compiled.Code != 0 {
				t.Fatalf("compile: %v %s", err, compiled.Stderr)
			}
			bundle, err := interp.LoadBundle([]byte(compiled.Bundle))
			if err != nil {
				t.Fatal(err)
			}
			var stderr bytes.Buffer
			vm := interp.New(bundle, interp.WithStderr(&stderr))
			defer vm.Close()
			if code := vm.Run(); code != 0 {
				t.Fatalf("Run() = %d: %s", code, stderr.String())
			}
		})
	}
}
