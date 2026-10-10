package interp_test

import (
	"bytes"
	"testing"

	"github.com/radkawar/diaspore/runtimes/go/dspc"
	"github.com/radkawar/diaspore/runtimes/go/interp"
)

func TestDictionaryInitializationProtocols(t *testing.T) {
	const source = `import types
assert type(dict.__init__) is types.WrapperDescriptorType
assert dict.__dict__['__init__'] is dict.__init__
target = {'old': 1}
assert dict.__init__(target) is None and target == {'old': 1}
assert target.__init__({'new': 2}, old=3) is None
assert target == {'old': 3, 'new': 2}
assert dict.__new__(dict, object(), ignored=True) == {}
assert dict.fromkeys(iter(['a', 'b']), 7) == {'a': 7, 'b': 7}
assert dict.fromkeys([]) == {}

class Mapping:
    def keys(self):
        return ['first', 'second']
    def __getitem__(self, key):
        if key == 'second':
            assert target['first'] == 'FIRST'
        return key.upper()
dict.__init__(target, Mapping())
assert target['first'] == 'FIRST' and target['second'] == 'SECOND'
assert dict(Mapping()) == {'first': 'FIRST', 'second': 'SECOND'}

class NativeSubclass(dict):
    def __setitem__(self, key, value):
        raise AssertionError('initialization invoked overridden setitem')
subclass = NativeSubclass()
assert subclass.__init__({'x': 1}) is None
assert subclass == {'x': 1}
class CustomNew(dict):
    def __new__(cls, source):
        result = dict.__new__(cls, source)
        assert len(result) == 0
        return result
calls = []
class Source:
    def keys(self):
        calls.append('keys')
        return ['key']
    def __getitem__(self, key):
        calls.append(key)
        return 7
assert CustomNew(Source()) == {'key': 7}
assert calls == ['keys', 'key']

class CustomSource(dict):
    def keys(self):
        return ['virtual']
    def __getitem__(self, key):
        return 9
assert dict(CustomSource()) == {'virtual': 9}

partial = {}
def pairs():
    yield ('first', 1)
    assert partial['first'] == 1
    raise RuntimeError('source failed')
try:
    dict.__init__(partial, pairs(), last=2)
except RuntimeError:
    pass
else:
    raise AssertionError('source error swallowed')
assert partial == {'first': 1}
try:
    partial.update([('second', 2), ('bad',)])
except ValueError:
    pass
else:
    raise AssertionError('invalid update pair accepted')
assert partial == {'first': 1, 'second': 2}
try:
    dict.__init__([], {})
except TypeError:
    pass
else:
    raise AssertionError('invalid dict receiver accepted')
namespace = globals()
dict.__init__(namespace, {'global_initialized': 42})
assert global_initialized == 42
`
	compiled, err := dspc.CompileSource("/src/main.py", source, dspc.CompileOptions{})
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
}
