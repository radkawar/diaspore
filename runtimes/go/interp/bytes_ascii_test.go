package interp_test

import (
	"bytes"
	"testing"

	"github.com/radkawar/diaspore/runtimes/go/dspc"
	"github.com/radkawar/diaspore/runtimes/go/interp"
)

func TestNativeBytesASCII(t *testing.T) {
	const source = `import types
for cls in (bytes, bytearray):
    assert cls().isascii() is True
    assert cls(range(128)).isascii() is True
    assert cls(b'\x00\t\n\r\x1f\x7f').isascii() is True
    for value in range(128, 256):
        assert cls([value]).isascii() is False
        assert cls(b'prefix' + bytes([value]) + b'suffix').isascii() is False
    descriptor = cls.__dict__['isascii']
    assert type(descriptor) is types.MethodDescriptorType
    assert descriptor is cls.isascii
    assert descriptor.__objclass__ is cls and descriptor.__name__ == 'isascii'
    assert descriptor(cls(b'abc')) is True
    assert descriptor.__get__(cls(b'\xff'), cls)() is False
    for operation in (lambda: cls().isascii(1), lambda: cls().isascii(extra=1), lambda: descriptor('text'), lambda: descriptor()):
        try:
            operation()
        except TypeError:
            pass
        else:
            raise AssertionError('invalid ASCII predicate call accepted')
    class Child(cls):
        pass
    assert Child(b'abc').isascii() is True
    assert Child(b'\x80').isascii() is False
    assert cls.isascii(Child(b'abc')) is True
mutable = bytearray(b'abc')
bound = mutable.isascii
assert bound() is True
mutable.append(128)
assert bound() is False
mutable.__init__(b'new')
assert bound() is True
mutable.__init__(b'\xff')
assert bound() is False
mutable.clear()
assert bound() is True
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
