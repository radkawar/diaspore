package interp_test

import (
	"bytes"
	"testing"

	"github.com/radkawar/diaspore/runtimes/go/dspc"
	"github.com/radkawar/diaspore/runtimes/go/interp"
)

func TestInheritedInplaceProtocols(t *testing.T) {
	cases := []struct{ name, source string }{
		{"list_subclass_identity", `class Values(list):
    pass
value = Values([1, 2])
original = value
value += (3, 4)
assert value is original and value == [1, 2, 3, 4]
value *= 2
assert value is original and value == [1, 2, 3, 4, 1, 2, 3, 4]
class Index:
    def __index__(self):
        return 0
value *= Index()
assert value is original and value == []
class SelfIterable(list):
    def __iter__(self):
        return iter([9])
value = SelfIterable([1, 2])
original = value
value += value
assert value is original and value == [1, 2, 9]
`},
		{"list_iteration_and_reflection", `class Right:
    def __iter__(self):
        return iter([2, 3])
    def __radd__(self, other):
        raise AssertionError('inplace list extension used reflected addition')
value = [1]
original = value
value += Right()
assert value is original and value == [1, 2, 3]
def broken():
    yield 4
    raise ValueError('iteration failed')
try:
    value += broken()
except ValueError as error:
    assert str(error) == 'iteration failed'
else:
    raise AssertionError('inplace extension swallowed iteration error')
assert value is original and value == [1, 2, 3, 4]
class Declines(list):
    def __iadd__(self, other):
        return NotImplemented
    def __add__(self, other):
        return 'ordinary addition'
value = Declines([1])
original = value
value += [2]
assert value == 'ordinary addition' and original == [1]
class Descriptor:
    def __get__(self, instance, owner):
        return lambda other: (instance, other)
class Described:
    __iadd__ = Descriptor()
value = Described()
original = value
value += 42
assert value == (original, 42)
`},
		{"dict_subclass_union", `from collections import defaultdict
value = defaultdict(int, {'a': 1})
original = value
value |= [('b', 2), ('c', 3)]
assert value is original and value.default_factory is int
assert list(value.items()) == [('a', 1), ('b', 2), ('c', 3)]
plain = {'a': 1}
original = plain
plain |= [('b', 2)]
assert plain is original and plain == {'a': 1, 'b': 2}
try:
    plain |= [('c', 3), ('bad',)]
except ValueError:
    pass
else:
    raise AssertionError('invalid dictionary update accepted')
assert plain == {'a': 1, 'b': 2, 'c': 3}
`},
		{"weak_proxy_and_pow_none", `from weakref import proxy
class Values(list):
    pass
value = Values([1, 2])
reference = proxy(value)
reference += [3]
assert reference is value and value == [1, 2, 3]
class Number(float):
    pass
number = Number(2)
reference = proxy(number)
assert pow(reference, 3.0, None) == 8.0
assert pow(2.0, 3.0, None) == 8.0
class Power:
    def __pow__(self, *args):
        return args
assert pow(Power(), 3, None) == (3,)
assert pow(Power(), 3, 5) == (3, 5)
assert pow(2, 3, 5) == 3
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
