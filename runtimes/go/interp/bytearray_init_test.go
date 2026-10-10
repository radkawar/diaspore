package interp_test

import (
	"bytes"
	"testing"

	"github.com/radkawar/diaspore/runtimes/go/dspc"
	"github.com/radkawar/diaspore/runtimes/go/interp"
)

func TestBytearrayReinitialization(t *testing.T) {
	const source = `target = bytearray(b'old')
assert target.__init__(b'new') is None and target == b'new'
assert target.__init__() is None and target == b''
target.__init__(source='é', encoding='utf-8')
assert target == b'\xc3\xa9'
target.__init__('é', 'ascii', errors='replace')
assert target == b'?'
source = bytearray(b'abcd')
view = memoryview(source)[::2]
target.__init__(view)
assert target == b'ac'
view.release()
target[0] = 120
assert source == b'abcd'
target.__init__(target)
assert target == b''
class Index:
    def __index__(self):
        return 3
target.__init__(Index())
assert target == b'\x00\x00\x00'
target.__init__([Index(), True, 255])
assert target == b'\x03\x01\xff'
class BadIndexIterable:
    def __index__(self):
        raise TypeError('index failed')
    def __iter__(self):
        return iter([65, 66])
assert bytearray(BadIndexIterable()) == b'AB'
for operation, error in (
    (lambda: target.__init__('text'), TypeError),
    (lambda: target.__init__(-1), ValueError),
    (lambda: target.__init__(1 << 100), OverflowError),
    (lambda: target.__init__(None), TypeError),
    (lambda: target.__init__(encoding='ascii'), TypeError),
    (lambda: target.__init__(errors='replace'), TypeError),
    (lambda: target.__init__(b'bytes', 'ascii'), TypeError),
    (lambda: target.__init__('é', 'ascii'), UnicodeEncodeError),
):
    target.__init__(b'old')
    try:
        operation()
    except error:
        assert target == b''
    else:
        raise AssertionError('invalid initialization accepted')
for operation in (
    lambda: target.__init__(b'new', encoding=None),
    lambda: target.__init__(b'new', unknown=True),
    lambda: target.__init__(b'new', source=b'duplicate'),
    lambda: target.__init__('new', 'utf-8\x00'),
):
    target.__init__(b'old')
    try:
        operation()
    except (TypeError, ValueError):
        assert target == b'old'
    else:
        raise AssertionError('invalid parser arguments accepted')
def broken():
    yield 65
    yield 66
    raise RuntimeError('iteration failed')
try:
    target.__init__(broken())
except RuntimeError as error:
    assert str(error) == 'iteration failed' and target == b'AB'
else:
    raise AssertionError('iteration failure suppressed')
try:
    target.__init__(iter([65, 300]))
except ValueError:
    assert target == b'A'
else:
    raise AssertionError('out of range byte accepted')
try:
    target.__init__([65, 300])
except ValueError:
    assert len(target) == 2 and target[0] == 65
else:
    raise AssertionError('out of range exact sequence byte accepted')
target.__init__(b'old')
view = memoryview(target)
for operation in (lambda: target.__init__(), lambda: target.__init__(b'new'), lambda: target.__init__(target)):
    try:
        operation()
    except BufferError:
        assert target == b'old' and view.tobytes() == b'old'
    else:
        raise AssertionError('exported buffer reinitialized')
view.release()
target.__init__()
view = memoryview(target)
assert target.__init__() is None
try:
    target.__init__(b'x')
except BufferError:
    assert target == b''
else:
    raise AssertionError('exported empty buffer resized')
view.release()
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
