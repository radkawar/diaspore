package interp_test

import (
	"bytes"
	"strings"
	"testing"

	"github.com/radkawar/diaspore/runtimes/go/dspc"
	"github.com/radkawar/diaspore/runtimes/go/interp"
)

func TestNativeHashEngines(t *testing.T) {
	const source = `import _diaspore_hash
import hashlib
import _hashcore
import sys
assert hashlib.__file__ == '<stdlib>/hashlib.py'
assert _hashcore._native_hash is _diaspore_hash.new
assert '_diaspore_hash' in sys.builtin_module_names
vectors = {
    'md5': '900150983cd24fb0d6963f7d28e17f72',
    'sha1': 'a9993e364706816aba3e25717850c26c9cd0d89d',
    'sha224': '23097d223405d8228642a477bda255b32aadbce4bda0b3f7e36c9da7',
    'sha256': 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad',
    'sha384': 'cb00753f45a35e8bb5a03d699ac65007272c32ab0eded1631a8b605a43ff5bed8086072ba1e7cc2358baeca134c825a7',
    'sha512': 'ddaf35a193617abacc417349ae20413112e6fa4e89a97ea20a9eeee64b55d39a2192992a274fc1a836ba3c23a3feebbd454d4423643ce80e2a9ac94fa54ca49f',
}
for name, expected in vectors.items():
    engine = _diaspore_hash.new(name)
    assert type(engine).__name__ == '_HashEngine'
    assert type(engine).__module__ == '_diaspore_hash'
    empty = engine.digest()
    assert engine.update(b'') is None
    assert engine.digest() == empty
    for byte in b'abc':
        assert engine.update(bytes([byte])) is None
    assert engine.hexdigest() == expected
    digest = engine.digest()
    assert type(digest) is bytes and digest.hex() == expected
    assert engine.digest() == digest and engine.hexdigest() == expected
    copied = engine.copy()
    assert copied is not engine and copied.digest() == digest
    copied.update(b'd')
    assert copied.digest() != digest and engine.digest() == digest
    engine.update(b'd')
    assert engine.digest() == copied.digest()
    mutable = bytearray(b'abc')
    live = _diaspore_hash.new(name)
    live.update(mutable)
    mutable[:] = b'xyz'
    assert live.hexdigest() == expected
    live.update(memoryview(mutable))
    reference = _diaspore_hash.new(name)
    reference.update(b'abcxyz')
    assert live.digest() == reference.digest()
    data = bytes(range(256)) * 5
    whole = _diaspore_hash.new(name)
    whole.update(data)
    for size in (1, 7, 63, 64, 65, 127, 128, 129):
        chunked = _diaspore_hash.new(name)
        for start in range(0, len(data), size):
            chunked.update(data[start:start + size])
        assert chunked.digest() == whole.digest()
    public = hashlib.new(name, b'abc')
    direct = getattr(hashlib, name)(b'abc')
    assert public.name == name and public.hexdigest() == direct.hexdigest() == expected
    assert public.digest_size == len(digest)
    assert public.block_size == (128 if name in ('sha384', 'sha512') else 64)
    public_copy = public.copy()
    assert public_copy.update(data) is None
    assert public.hexdigest() == expected and public_copy.digest() != public.digest()
    public.update(data)
    assert public.digest() == public_copy.digest()
engine = _diaspore_hash.new('sha256')
view = memoryview(b'abcd')[::2]
released = memoryview(b'abcd')
released.release()
for operation, expected in (
    (lambda: _diaspore_hash.new('unknown'), ValueError),
    (lambda: _diaspore_hash.new(b'sha256'), TypeError),
    (lambda: _diaspore_hash.new(name='sha256'), TypeError),
    (lambda: engine.update('text'), TypeError),
    (lambda: engine.update([1, 2]), TypeError),
    (lambda: engine.update(view), BufferError),
    (lambda: engine.update(released), ValueError),
    (lambda: engine.update(), TypeError),
    (lambda: engine.update(data=b'a'), TypeError),
    (lambda: engine.copy(1), TypeError),
    (lambda: engine.copy(extra=1), TypeError),
    (lambda: engine.digest(1), TypeError),
    (lambda: engine.digest(extra=1), TypeError),
    (lambda: engine.hexdigest(1), TypeError),
    (lambda: engine.hexdigest(extra=1), TypeError),
):
    try:
        operation()
    except expected:
        pass
    else:
        raise AssertionError('invalid native hash call accepted')
assert engine.hexdigest() == 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'
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

func TestNativeHashFuel(t *testing.T) {
	source := "import _diaspore_hash\nh = _diaspore_hash.new('sha256')\nprint('before update')\nh.update(b'" + strings.Repeat("a", 4096) + "')\nprint('unmetered')\n"
	compiled, err := dspc.CompileSource("/src/main.py", source, dspc.CompileOptions{})
	if err != nil || compiled.Code != 0 {
		t.Fatalf("compile: %v %s", err, compiled.Stderr)
	}
	bundle, err := interp.LoadBundle([]byte(compiled.Bundle))
	if err != nil {
		t.Fatal(err)
	}
	var output bytes.Buffer
	vm := interp.New(bundle, interp.WithStdout(&output), interp.WithStderr(&output), interp.WithMaxSteps(1000))
	defer vm.Close()
	if code := vm.Run(); code != 3 || !strings.Contains(output.String(), "before update") || !strings.Contains(output.String(), "step budget exceeded") || strings.Contains(output.String(), "unmetered") {
		t.Fatalf("unmetered native hashing: code=%d output=%s", code, output.String())
	}
}
