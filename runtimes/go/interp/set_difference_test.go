package interp_test

import (
	"bytes"
	"testing"

	"github.com/radkawar/diaspore/runtimes/go/dspc"
	"github.com/radkawar/diaspore/runtimes/go/interp"
)

func TestSetDifferenceUpdate(t *testing.T) {
	cases := []struct{ name, source string }{
		{"variadic_descriptor", `value = {1, 2, 3, 4, 5}
original = value
assert value.difference_update() is None
assert value == {1, 2, 3, 4, 5}
assert value.difference_update([1], (2,), {3}, frozenset([4])) is None
assert value is original and value == {5}
assert set.difference_update(value, [5, 6]) is None
assert value == set()
assert not hasattr(frozenset(), 'difference_update')
try:
    set.difference_update([], [1])
except TypeError:
    pass
else:
    raise AssertionError('descriptor accepted a non-set receiver')
value = {1, 2}
try:
    value.difference_update([1], other=[2])
except TypeError:
    pass
else:
    raise AssertionError('keywords accepted')
assert value == {1, 2}
`},
		{"partial_mutation", `events = []
def broken():
    events.append('start')
    yield 1
    events.append('after first')
    raise ValueError('source failed')
value = {1, 2, 3}
try:
    value.difference_update([3], broken(), [2])
except ValueError as error:
    assert str(error) == 'source failed'
else:
    raise AssertionError('iterator error swallowed')
assert value == {2} and events == ['start', 'after first']
try:
    value.difference_update([2, []])
except TypeError:
    pass
else:
    raise AssertionError('unhashable iterable member accepted')
assert value == set()
for item in ([], set()):
    try:
        value.difference_update([item])
    except TypeError:
        pass
    else:
        raise AssertionError('empty target skipped hashing')
value = {frozenset([1])}
try:
    value.difference_update([{1}])
except TypeError:
    pass
else:
    raise AssertionError('difference_update implicitly froze a set')
assert value == {frozenset([1])}
`},
		{"self_alias_and_subclass", `class Values(set):
    def __iter__(self):
        raise AssertionError('native set operation called overridden iterator')
value = Values([1, 2, 3])
assert value.difference_update(value) is None
assert len(value) == 0
value = {1, 2, 3}
assert value.difference_update(Values([1, 3])) is None
assert value == {2}
value.difference_update(value, [2])
assert value == set()
class Broken:
    def __iter__(self):
        raise ValueError('later argument inspected')
value = {1, 2}
try:
    value.difference_update(value, Broken())
except ValueError as error:
    assert str(error) == 'later argument inspected'
else:
    raise AssertionError('self clearing stopped variadic processing')
assert value == set()
`},
		{"stored_hash_and_collisions", `hashes = []
locked = False
class Key:
    def __init__(self, name):
        self.name = name
    def __hash__(self):
        if locked:
            raise AssertionError('native set member rehashed')
        hashes.append(self.name)
        return 7
    def __eq__(self, other):
        return self is other
first, second, third = Key('first'), Key('second'), Key('third')
value = {first, second, third}
source = frozenset([first, third])
before = len(hashes)
locked = True
assert value.difference_update(source) is None
assert len(hashes) == before
assert len(value) == 1 and next(iter(value)) is second
value.difference_update(value)
assert len(value) == 0 and len(hashes) == before
locked = False
keys = [Key(str(index)) for index in range(20)]
value = {keys[0]}
source = set(keys)
before = len(hashes)
locked = True
value.difference_update(source)
assert len(value) == 0 and len(hashes) == before
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
