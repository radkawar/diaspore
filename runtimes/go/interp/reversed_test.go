package interp_test

import (
	"bytes"
	"testing"

	"github.com/radkawar/diaspore/runtimes/go/dspc"
	"github.com/radkawar/diaspore/runtimes/go/interp"
)

func TestReversedProtocols(t *testing.T) {
	cases := []struct{ name, source string }{
		{"builtin_sequences", `assert list(reversed([1, 2, 3])) == [3, 2, 1]
assert list(reversed((1, 2, 3))) == [3, 2, 1]
assert list(reversed('a\U0001f600\ud800z')) == ['z', '\ud800', '\U0001f600', 'a']
assert list(reversed(b'abc')) == [99, 98, 97]
assert list(reversed(bytearray(b'abc'))) == [99, 98, 97]
assert list(reversed(memoryview(b'abc'))) == [99, 98, 97]
assert list(reversed(range(1, 7, 2))) == [5, 3, 1]
assert list(reversed({'a': 1, 'b': 2})) == ['b', 'a']
assert list(list.__reversed__([1, 2])) == [2, 1]
try:
    reversed([1], unexpected=True)
except TypeError:
    pass
else:
    raise AssertionError('reversed accepted keywords')
`},
		{"live_sequences", `values = [1, 2, 3]
it = reversed(values)
values[2] = 9
values.append(4)
assert next(it) == 9
values[1] = 8
assert list(it) == [8, 1]
values = [1, 2, 3]
it = reversed(values)
values.pop()
assert next(it, 'done') == 'done'
values.extend([4, 5])
assert next(it, 'done') == 'done'
class NativeList(list):
    def __getitem__(self, index):
        raise AssertionError('list reverse called subclass indexing')
    def __len__(self):
        raise AssertionError('list reverse called subclass length')
assert list(reversed(NativeList([1, 2, 3]))) == [3, 2, 1]
class NativeTuple(tuple):
    def __getitem__(self, index):
        return index + 10
assert list(reversed(NativeTuple((1, 2, 3)))) == [12, 11, 10]
`},
		{"special_lookup", `class Custom:
    def __reversed__(self):
        return 42
    def __getattribute__(self, name):
        if name == '__reversed__':
            raise AssertionError('special lookup used attribute hook')
        return object.__getattribute__(self, name)
value = Custom()
value.__reversed__ = lambda: 99
assert reversed(value) == 42
class Static:
    @staticmethod
    def __reversed__():
        return 17
assert reversed(Static()) == 17
class Descriptor:
    def __get__(self, instance, owner):
        return lambda: (instance, owner)
class Described:
    __reversed__ = Descriptor()
value = Described()
assert reversed(value) == (value, Described)
class Disabled(list):
    __reversed__ = None
try:
    reversed(Disabled([1]))
except TypeError as error:
    assert str(error) == "'Disabled' object is not reversible"
else:
    raise AssertionError('disabled __reversed__ ignored')
class Meta(type):
    def __reversed__(cls):
        return ('class', cls)
class ClassValue(metaclass=Meta):
    pass
assert reversed(ClassValue) == ('class', ClassValue)
`},
		{"sequence_fallback", `events = []
class Sequence:
    def __len__(self):
        events.append('len')
        return 3
    def __getitem__(self, index):
        events.append(index)
        return index + 10
value = Sequence()
value.__reversed__ = lambda: 99
it = reversed(value)
assert events == ['len']
assert next(it) == 12
assert events == ['len', 2]
assert list(it) == [11, 10]
assert events == ['len', 2, 1, 0]
class StopSequence:
    def __init__(self, exception):
        self.exception = exception
        self.calls = 0
    def __len__(self):
        return 2
    def __getitem__(self, index):
        self.calls += 1
        raise self.exception
for exception in (IndexError('end'), StopIteration('end'), ValueError('failure')):
    value = StopSequence(exception)
    it = reversed(value)
    if isinstance(exception, ValueError):
        try:
            next(it)
        except ValueError as error:
            assert str(error) == 'failure'
        else:
            raise AssertionError('reverse swallowed an error')
    else:
        assert next(it, 'done') == 'done'
    assert next(it, 'done') == 'done'
    assert value.calls == 1
class LengthOnly:
    def __len__(self):
        raise AssertionError('nonsequence length was called')
try:
    reversed(LengthOnly())
except TypeError:
    pass
else:
    raise AssertionError('length-only object is reversible')
class NegativeLength(Sequence):
    def __len__(self):
        return -1
try:
    reversed(NegativeLength())
except ValueError:
    pass
else:
    raise AssertionError('negative length accepted')
`},
		{"stdlib_reverse_hooks", `from collections import deque
from weakref import proxy
assert list(reversed(deque([1, 2, 3]))) == [3, 2, 1]
class Values(list):
    pass
value = Values([1, 2, 3])
reference = proxy(value)
assert list(reversed(reference)) == [3, 2, 1]
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
