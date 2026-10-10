package interp_test

import (
	"bytes"
	"testing"

	"github.com/radkawar/diaspore/runtimes/go/dspc"
	"github.com/radkawar/diaspore/runtimes/go/interp"
)

func TestNativeDescriptorIdentityHashes(t *testing.T) {
	cases := []struct{ name, source string }{
		{"descriptor_dictionary_keys", `class Slots:
    __slots__ = ('member',)
descriptors = (
    object.__repr__, object.__str__, object.__eq__, object.__hash__,
    int.__hash__, str.join, type.__dict__['__mro__'], Slots.member,
)
assert object.__repr__ is object.__dict__['__repr__']
assert int.__hash__ is int.__dict__['__hash__']
assert Slots.member is Slots.__dict__['member']
assert len(set(descriptors)) == len(descriptors)
for index, descriptor in enumerate(descriptors):
    before = hash(descriptor)
    mapping = {descriptor: index}
    assert mapping[descriptor] == index
    assert hash(descriptor) == before
    assert mapping.pop(descriptor) == index
    assert not mapping
    assert {(descriptor,): index}[(descriptor,)] == index
`},
		{"vendored_pprint_dispatch", `import pprint
assert pprint.pformat({'z': 1, 'a': [1, 2]}) == "{'a': [1, 2], 'z': 1}"
assert pprint.pformat([1, 2, 3], width=4) == '[1,\n 2,\n 3]'
assert list.__repr__ is not dict.__repr__
assert list.__repr__ in pprint.PrettyPrinter._dispatch
assert dict.__repr__ in pprint.PrettyPrinter._dispatch
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
			vm := interp.New(bundle, interp.WithSnippetCompiler(dspc.CompileSnippet), interp.WithStderr(&stderr))
			defer vm.Close()
			if code := vm.Run(); code != 0 {
				t.Fatalf("Run() = %d: %s", code, stderr.String())
			}
		})
	}
}
