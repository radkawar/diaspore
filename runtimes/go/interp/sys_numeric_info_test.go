package interp_test

import (
	"bytes"
	"math/bits"
	"strconv"
	"testing"

	"github.com/radkawar/diaspore/runtimes/go/dspc"
	"github.com/radkawar/diaspore/runtimes/go/interp"
)

func TestNativeIntegerHashInfo(t *testing.T) {
	source := `import sys
import types
assert sys.int_info.bits_per_digit == ` + strconv.Itoa(bits.UintSize) + `
assert sys.int_info.sizeof_digit * 8 == sys.int_info.bits_per_digit
assert sys.int_info.default_max_str_digits == 0
assert sys.int_info.str_digits_check_threshold == 0
assert len(str(int('1' * 5000))) == 5000
h = sys.hash_info
assert tuple(h) == (64, (1 << 61) - 1, 314159, 0, 1000003, 'fnv1a_64', 63, 0, 0)
assert hash(float('inf')) == h.inf and hash(-float('inf')) == -h.inf
assert hash(h.modulus) == 0 and hash(h.modulus + 1) == 1
assert hash(complex(2, 3)) == hash(2) + h.imag * hash(3)
assert 0 <= hash('deterministic') < (1 << h.hash_bits)
assert 0 <= hash(b'deterministic') < (1 << h.hash_bits)
assert sys.flags.hash_randomization == 0
for name, count in (('int_info', 4), ('hash_info', 9)):
    info = getattr(sys, name)
    cls = type(info)
    assert isinstance(info, tuple) and len(info) == count
    assert cls.__name__ == name and cls.__module__ == 'sys'
    assert cls.__bases__ == (tuple,)
    assert (info.n_fields, info.n_sequence_fields, info.n_unnamed_fields) == (count, count, 0)
    assert not hasattr(info, '__dict__')
    assert repr(info).startswith('sys.' + name + '(')
    assert str(info) == repr(info)
    assert cls(tuple(info)) == info
    assert cls(sequence=info, dict={}) == info
    constructor, args = info.__reduce__()
    assert constructor is cls and constructor(*args) == info
    assert hash(info) == hash(tuple(info))
    for field, value in zip(cls.__match_args__, info):
        member = cls.__dict__[field]
        assert type(member) is types.MemberDescriptorType
        assert member.__objclass__ is cls and member.__name__ == field
        assert member.__get__(info) == value == getattr(info, field)
        for operation in (lambda: setattr(info, field, 1), lambda: delattr(info, field), lambda: member.__set__(info, 1)):
            try:
                operation()
            except AttributeError as error:
                assert str(error) == 'readonly attribute'
            else:
                raise AssertionError('mutable system information')
    for length in (count - 1, count + 1):
        try:
            cls([0] * length)
        except TypeError as error:
            assert str(error).startswith('sys.' + name + '() takes a ')
        else:
            raise AssertionError('invalid struct sequence size')
    try:
        cls.extra = 1
    except TypeError:
        pass
    else:
        raise AssertionError('mutable native class')
    try:
        class Child(cls):
            pass
    except TypeError:
        pass
    else:
        raise AssertionError('subclassable system information')
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
