package interp_test

import (
	"bytes"
	"testing"

	"github.com/radkawar/diaspore/runtimes/go/dspc"
	"github.com/radkawar/diaspore/runtimes/go/interp"
)

func TestBoundPositionalCallPreservesDescriptorsAndMutation(t *testing.T) {
	const source = `events = []
class Base:
    def method(self, first, second):
        return (self, first, second)
    def one(self, value):
        return value + 1
    @classmethod
    def class_method(cls, value):
        return (cls, value)
    @staticmethod
    def static_method(value):
        return value * 2
    @property
    def selected(self):
        events.append('get')
        return self.method
    def defaults(self, value=12):
        return value
    def generator(self, value):
        yield value
class Child(Base):
    def parent(self, value):
        return super().one(value)
item = Child()
def argument(value):
    events.append(value)
    return value
assert item.selected(argument(2), argument(3)) == (item, 2, 3)
assert events == ['get', 2, 3]
assert item.one(item.one(item.one(0))) == 3
assert item.class_method(7) == (Child, 7)
assert item.static_method(7) == 14
assert item.parent(7) == 8
assert item.defaults() == 12
assert item.defaults(value=9) == 9
assert list(item.generator(8)) == [8]
saved = item.method
assert saved(4, 5) == (item, 4, 5)
class Descriptor:
    def __get__(self, instance, owner):
        events.append('descriptor')
        return instance.method
class Custom(Child):
    chosen = Descriptor()
    def __getattribute__(self, name):
        if name == 'alias':
            events.append('hook')
            return object.__getattribute__(self, 'method')
        return object.__getattribute__(self, name)
custom = Custom()
events.clear()
assert custom.chosen(argument(1), argument(2)) == (custom, 1, 2)
assert events == ['descriptor', 1, 2]
events.clear()
assert custom.alias(argument(3), argument(4)) == (custom, 3, 4)
assert events == ['hook', 3, 4]
def replacement(self, first, second):
    return ('replaced', self, first, second)
def replace_code():
    Base.method.__code__ = replacement.__code__
    return 11
assert saved(replace_code(), 12) == ('replaced', item, 11, 12)
assert saved(13, 14) == ('replaced', item, 13, 14)
def reject():
    events.append('argument failure')
    raise ValueError('stop before method dispatch')
events.clear()
try:
    saved(reject(), argument(2))
except ValueError:
    pass
else:
    raise AssertionError('argument exception did not propagate')
assert events == ['argument failure']
assert saved(15, 16) == ('replaced', item, 15, 16)
item.method = lambda left, right: ('shadow', left, right)
assert item.method(1, 2) == ('shadow', 1, 2)
assert saved(1, 2) == ('replaced', item, 1, 2)
`
	compiled, err := dspc.CompileSource("/src/methods.py", source, dspc.CompileOptions{})
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
}
