package interp_test

import (
	"bytes"
	"testing"

	"github.com/radkawar/diaspore/runtimes/go/dspc"
	"github.com/radkawar/diaspore/runtimes/go/interp"
)

func TestNativeBytesFromHex(t *testing.T) {
	const source = `import types
for cls in (bytes, bytearray):
    assert cls.fromhex('') == cls()
    assert cls.fromhex(' \t\n\r\v\f ') == cls()
    assert cls.fromhex('00aBff 10\t20\n30\r40\v50\f60') == cls(b'\x00\xab\xff\x10\x20\x30\x40\x50\x60')
    assert type(cls.fromhex('00')) is cls
    assert cls().fromhex('4142') == cls(b'AB')
    assert cls.fromhex(''.join(format(n, '02x') for n in range(256))) == cls(range(256))
    for text, position in (('a', 1), ('a ', 1), ('ab c', 4), ('0g', 1), ('g0', 0), ('01 2 3', 4), ('0x01', 1), ('\x1c', 0), ('\x00', 0), ('\u00a0', 0), ('00\U0001f600', 2), ('?\u00e9', 1), ('00\ud800', 2)):
        try:
            cls.fromhex(text)
        except ValueError as error:
            assert str(error) == 'non-hexadecimal number found in fromhex() arg at position ' + str(position)
        else:
            raise AssertionError('invalid hexadecimal accepted')
    for bad in (b'00', bytearray(b'00'), memoryview(b'00'), 0, None):
        try:
            cls.fromhex(bad)
        except TypeError:
            pass
        else:
            raise AssertionError('non-string hexadecimal accepted')
    try:
        cls.fromhex(string='00')
    except TypeError:
        pass
    else:
        raise AssertionError('keyword accepted')
    descriptor = cls.__dict__['fromhex']
    assert type(descriptor) is types.ClassMethodDescriptorType
    assert descriptor.__objclass__ is cls and descriptor.__name__ == 'fromhex'
    assert descriptor(cls, '41') == cls(b'A')
    assert descriptor.__get__(None, cls)('41') == cls(b'A')
    try:
        descriptor(int, '00')
    except TypeError:
        pass
    else:
        raise AssertionError('invalid classmethod receiver accepted')
class Text(str):
    def __str__(self):
        raise AssertionError('fromhex called __str__')
assert bytes.fromhex(Text('41')) == b'A'
seen = []
class Binary(bytes):
    def __new__(cls, value):
        seen.append(('new', cls, type(value), value))
        return super().__new__(cls, value)
    def __init__(self, value):
        seen.append(('init', type(self), type(value), value))
result = Binary.fromhex('4142')
assert type(result) is Binary and result == b'AB'
assert seen == [('new', Binary, bytes, b'AB'), ('init', Binary, bytes, b'AB')]
seen.clear()
class Mutable(bytearray):
    def __init__(self, value):
        seen.append((type(value), value))
        super().__init__(value)
result = Mutable.fromhex('4142')
assert type(result) is Mutable and result == bytearray(b'AB')
assert seen == [(bytes, b'AB')]
result[0] = 67
assert result == bytearray(b'CB')
class Alternate(bytes):
    def __new__(cls, value):
        return ('constructed', value)
assert Alternate.fromhex('41') == ('constructed', b'A')
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
