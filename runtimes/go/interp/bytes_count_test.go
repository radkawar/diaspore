package interp_test

import (
	"bytes"
	"testing"

	"github.com/radkawar/diaspore/runtimes/go/dspc"
	"github.com/radkawar/diaspore/runtimes/go/interp"
)

func TestNativeBytesCount(t *testing.T) {
	const source = `import types
class Index:
    def __init__(self, value):
        self.value = value
    def __index__(self):
        return self.value
class BadIndex:
    def __index__(self):
        return 1.0
class Binary(bytes):
    def __index__(self):
        raise AssertionError('buffer needle must take precedence')
class Meta(type):
    def __index__(cls):
        return 97
class Letter(metaclass=Meta):
    pass
for cls in (bytes, bytearray):
    data = cls(b'ababa\x00\x01\xff')
    assert data.count(b'aba') == 1
    assert data.count(b'a') == data.count(97) == data.count(Index(97)) == data.count(Letter) == 3
    assert data.count(bytearray(b'ba')) == data.count(memoryview(b'ba')) == 2
    assert data.count(Binary(b'a')) == 3
    assert data.count(False) == data.count(True) == data.count(255) == 1
    assert data.count(b'a', 1, 4) == 1
    assert data.count(b'a', Index(-8), Index(-3)) == 3
    assert data.count(b'a', None, None) == 3
    assert data.count(b'a', -(1 << 100), 1 << 100) == 3
    assert data.count(b'a', 1 << 100) == 0
    assert data.count(b'a', 0, -(1 << 100)) == 0
    assert cls(b'aaaaa').count(b'aa') == 2
    assert cls(b'\xc3\xa9').count(b'') == 3
    assert data.count(b'') == len(data) + 1
    assert data.count(b'', len(data)) == 1
    assert data.count(b'', len(data) + 1) == 0
    assert data.count(b'', 4, 3) == 0
    assert cls().count(b'') == 1
    descriptor = cls.__dict__['count']
    assert type(descriptor) is types.MethodDescriptorType
    assert descriptor is cls.count and descriptor.__objclass__ is cls
    assert descriptor(data, b'a') == descriptor.__get__(data, cls)(97) == 3
    class Child(cls):
        pass
    assert Child(b'aaa').count(97) == cls.count(Child(b'aaa'), b'aa') + 2
    for operation, error in (
        (lambda: data.count(-1), ValueError),
        (lambda: data.count(256), ValueError),
        (lambda: data.count(1 << 100), ValueError),
        (lambda: data.count(-1 << 100), ValueError),
        (lambda: data.count('a'), TypeError),
        (lambda: data.count([97]), TypeError),
        (lambda: data.count(BadIndex()), TypeError),
        (lambda: data.count(b'a', 1.0), TypeError),
        (lambda: data.count(b'a', BadIndex()), TypeError),
        (lambda: data.count(b'a', start=1), TypeError),
        (lambda: data.count(), TypeError),
        (lambda: data.count(b'a', 0, 1, 2), TypeError),
        (lambda: descriptor('text', b't'), TypeError),
        (lambda: data.count(memoryview(b'abcd')[::2]), BufferError),
    ):
        try:
            operation()
        except error:
            pass
        else:
            raise AssertionError('invalid binary count accepted')
log = []
class LoggedIndex:
    def __init__(self, name, value):
        self.name, self.value = name, value
    def __index__(self):
        log.append(self.name)
        return self.value
assert b'aaa'.count(LoggedIndex('needle', 97), LoggedIndex('start', 0), LoggedIndex('end', 3)) == 3
assert log == ['start', 'end', 'needle']
mutable = bytearray(b'aaa')
bound = mutable.count
mutable.__init__(b'bbb')
assert bound(b'a') == 0 and bound(b'b') == 3
view = memoryview(b'a')
view.release()
try:
    b'aaa'.count(view)
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
