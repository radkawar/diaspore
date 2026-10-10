package interp_test

import (
	"bytes"
	"testing"

	"github.com/radkawar/diaspore/runtimes/go/dspc"
	"github.com/radkawar/diaspore/runtimes/go/interp"
)

func TestNativeBytesRfind(t *testing.T) {
	const source = `import types
class Index:
    def __init__(self, value):
        self.value = value
    def __index__(self):
        return self.value
class Binary(bytes):
    def __index__(self):
        raise AssertionError('buffer needle must take precedence')
for cls in (bytes, bytearray):
    data = cls(b'ababa\x00\xff')
    assert data.rfind(b'aba') == 2
    assert data.rfind(b'a') == data.rfind(97) == data.rfind(Index(97)) == 4
    assert data.rfind(Binary(b'a')) == 4
    assert data.rfind(bytearray(b'ba')) == data.rfind(memoryview(b'ba')) == 3
    assert data.rfind(0) == 5 and data.rfind(255) == 6
    assert data.rfind(b'missing') == -1
    assert data.rfind(b'aba', 0, 4) == 0
    assert data.rfind(b'aba', 1, 4) == -1
    assert data.rfind(b'a', Index(-7), Index(-2)) == 4
    assert data.rfind(b'a', None, None) == 4
    assert data.rfind(b'a', -(1 << 100), 1 << 100) == 4
    assert data.rfind(b'a', 1 << 100) == -1
    assert data.rfind(b'a', 0, -(1 << 100)) == -1
    assert data.rfind(b'', len(data)) == len(data)
    assert data.rfind(b'', len(data) + 1) == -1
    assert data.rfind(b'', 4, 3) == -1
    assert data.rfind(b'', 1, 3) == 3
    assert cls().rfind(b'') == 0
    assert cls(b'\xc3\xa9').rfind(b'') == 2
    descriptor = cls.__dict__['rfind']
    assert type(descriptor) is types.MethodDescriptorType
    assert descriptor is cls.rfind and descriptor.__objclass__ is cls
    assert descriptor(data, b'a') == descriptor.__get__(data, cls)(97) == 4
    class Child(cls):
        pass
    assert Child(b'ababa').rfind(b'aba') == 2
    assert cls.rfind(Child(b'ababa'), b'aba', 0, 4) == 0
    for operation, error in (
        (lambda: data.rfind(-1), ValueError),
        (lambda: data.rfind(256), ValueError),
        (lambda: data.rfind(1 << 100), ValueError),
        (lambda: data.rfind('a'), TypeError),
        (lambda: data.rfind(b'a', 1.0), TypeError),
        (lambda: data.rfind(b'a', start=1), TypeError),
        (lambda: data.rfind(), TypeError),
        (lambda: data.rfind(b'a', 0, 1, 2), TypeError),
        (lambda: descriptor('text', b't'), TypeError),
        (lambda: data.rfind(memoryview(b'abcd')[::2]), BufferError),
    ):
        try:
            operation()
        except error:
            pass
        else:
            raise AssertionError('invalid binary reverse search accepted')
mutable = bytearray(b'ababa')
bound = mutable.rfind
mutable.__init__(b'xxa')
assert bound(b'a') == 2 and bound(b'ba') == -1
view = memoryview(b'a')
view.release()
try:
    b'aaa'.rfind(view)
except ValueError:
    pass
else:
    raise AssertionError('released needle buffer accepted')
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
