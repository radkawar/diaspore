package interp_test

import (
	"bytes"
	"testing"

	"github.com/radkawar/diaspore/runtimes/go/dspc"
	"github.com/radkawar/diaspore/runtimes/go/interp"
)

func TestMetaclassIterationProtocols(t *testing.T) {
	const source = `class Meta(type):
    def __iter__(cls):
        return iter((1, 2, 3))
class Values(metaclass=Meta):
    def __iter__(self):
        raise AssertionError('class namespace used instead of metaclass')
a, b, c = Values
assert (a, b, c) == (1, 2, 3)
assert list(Values) == [1, 2, 3]

class IteratorMeta(type):
    def __iter__(cls):
        return cls
    def __next__(cls):
        cls.remaining -= 1
        if cls.remaining < 0:
            raise StopIteration('finished')
        return cls.remaining
class Iterator(metaclass=IteratorMeta):
    remaining = 2
assert iter(Iterator) is Iterator
assert next(Iterator) == 1 and next(Iterator) == 0
try:
    next(Iterator)
except StopIteration as error:
    assert error.value == 'finished'
else:
    raise AssertionError('class iterator did not stop')
assert next(Iterator, 'default') == 'default'

seen = []
class Descriptor:
    def __get__(self, instance, owner):
        seen.append((instance, owner))
        return lambda: iter((4, 5))
class DescriptorMeta(type):
    __iter__ = Descriptor()
class Described(metaclass=DescriptorMeta):
    pass
assert list(Described) == [4, 5]
assert seen == [(Described, DescriptorMeta)]

class InvalidMeta(type):
    def __iter__(cls):
        return [1]
class Invalid(metaclass=InvalidMeta):
    pass
try:
    iter(Invalid)
except TypeError as error:
    assert str(error) == "iter() returned non-iterator of type 'list'"
else:
    raise AssertionError('non-iterator return accepted')

class OrdinaryIterator:
    def __iter__(self):
        return self
    def __next__(self):
        raise StopIteration
ordinary = OrdinaryIterator()
assert iter(ordinary) is ordinary
assert list(ordinary) == []
`
	compiled, err := dspc.CompileSource("/src/main.py", source, dspc.CompileOptions{})
	if err != nil || compiled.Code != 0 {
		t.Fatalf("compile: %v %s", err, compiled.Stderr)
	}
	bundle, err := interp.LoadBundle([]byte(compiled.Bundle))
	if err != nil {
		t.Fatal(err)
	}
	var output bytes.Buffer
	vm := interp.New(bundle, interp.WithStdout(&output), interp.WithStderr(&output))
	defer vm.Close()
	if code := vm.Run(); code != 0 {
		t.Fatalf("Run() = %d: %s", code, output.String())
	}
}
