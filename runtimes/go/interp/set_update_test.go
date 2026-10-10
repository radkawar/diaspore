package interp_test

import (
	"bytes"
	"testing"

	"github.com/radkawar/diaspore/runtimes/go/dspc"
	"github.com/radkawar/diaspore/runtimes/go/interp"
)

func TestSetUpdate(t *testing.T) {
	cases := []struct{ name, source string }{
		{"variadic_descriptor", `value = {1}
original = value
assert value.update() is None
assert value.update([2, 2], (3,), {4}, frozenset([5]), {'six': 6}, 'ab', b'c') is None
assert value is original and value == {1, 2, 3, 4, 5, 'six', 'a', 'b', 99}
assert set.update(value, [7]) is None and 7 in value
assert not hasattr(frozenset(), 'update')
assert set.update.__objclass__ is set
try:
    set.update([], [1])
except TypeError:
    pass
else:
    raise AssertionError('non-set descriptor receiver accepted')
value = {1}
try:
    value.update([2], other=[3])
except TypeError:
    pass
else:
    raise AssertionError('keywords accepted')
assert value == {1}
`},
		{"subclass_iteration_and_aliases", `class NativeSet(set):
    def __iter__(self):
        raise AssertionError('native set iterator override called')
    def add(self, value):
        raise AssertionError('native set add override called')
class NativeFrozen(frozenset):
    def __iter__(self):
        raise AssertionError('native frozenset iterator override called')
value = NativeSet([1])
assert value.update(value, NativeSet([2]), NativeFrozen([3])) is None
assert value == {1, 2, 3}
assert value.update(value) is None and value == {1, 2, 3}
class Mapping(dict):
    def __iter__(self):
        return iter(['virtual'])
value = set()
value.update(Mapping({'stored': 42}))
assert value == {'virtual'}
value.update(iter(value))
assert value == {'virtual'}
`},
		{"partial_mutation_and_errors", `events = []
def broken():
    events.append('start')
    yield 2
    events.append('next')
    raise ValueError('iterator failed')
class Later:
    def __iter__(self):
        events.append('later')
        return iter([4])
value = set()
try:
    value.update([1], broken(), Later())
except ValueError as error:
    assert str(error) == 'iterator failed'
else:
    raise AssertionError('iterator error swallowed')
assert value == {1, 2} and events == ['start', 'next']
try:
    value.update([3, [], 4])
except TypeError:
    pass
else:
    raise AssertionError('unhashable member accepted')
assert value == {1, 2, 3}
try:
    value.update([{5}])
except TypeError:
    pass
else:
    raise AssertionError('mutable member implicitly frozen')
class HashFailure:
    def __hash__(self):
        raise RuntimeError('hash failed')
try:
    value.update([4, HashFailure(), 6])
except RuntimeError as error:
    assert str(error) == 'hash failed'
else:
    raise AssertionError('hash error swallowed')
assert value == {1, 2, 3, 4}
class Colliding:
    def __hash__(self):
        return 12345
    def __eq__(self, other):
        raise ValueError('equality failed')
first, second = Colliding(), Colliding()
value = {first}
try:
    value.update([7, second, 8])
except ValueError as error:
    assert str(error) == 'equality failed'
else:
    raise AssertionError('equality error swallowed')
assert len(value) == 2 and 7 in value and 8 not in value
`},
		{"native_cached_hashes_and_clean_merge", `hashes = []
locked = False
class Key:
    def __init__(self, name, number):
        self.name, self.number = name, number
    def __hash__(self):
        if locked:
            raise AssertionError('native key rehashed')
        hashes.append(self.name)
        return self.number
    def __eq__(self, other):
        if locked:
            raise AssertionError('clean native merge compared source keys')
        return self is other
first, second = Key('first', 7), Key('second', 7)
source = {first, second}
frozen = frozenset(source)
before = len(hashes)
locked = True
for other in (source, frozen):
    value = set()
    assert value.update(other) is None
    assert len(value) == 2 and len(hashes) == before
    value.update(value)
    assert len(value) == 2 and len(hashes) == before
locked = False
third = Key('third', 8)
dictionary = {first: 1, third: 3, 'text': 4}
before = len(hashes)
locked = True
value = set()
value.update(dictionary)
assert len(value) == 3 and len(hashes) == before
assert 'text' in value
locked = False
class Mapping(dict):
    pass
mapping = Mapping({third: 3})
hashes.clear()
value = set()
value.update(mapping)
assert hashes == ['third'] and len(value) == 1
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
