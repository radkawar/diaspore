package interp_test

import (
	"bytes"
	"testing"

	"github.com/radkawar/diaspore/runtimes/go/dspc"
	"github.com/radkawar/diaspore/runtimes/go/interp"
)

func TestNativeFloatInfo(t *testing.T) {
	const source = `import sys
import types
info = sys.float_info
cls = type(info)
expected = (1.7976931348623157e308, 1024, 308, 2.2250738585072014e-308, -1021, -307, 15, 53, 2.220446049250313e-16, 2, 1)
assert isinstance(info, tuple) and len(info) == 11
assert tuple(info) == expected and info == expected and hash(info) == hash(expected)
assert info[0] == expected[0] and info[-1] == 1 and info[1:3] == (1024, 308)
assert cls.__name__ == 'float_info' and cls.__module__ == 'sys'
assert cls.__bases__ == (tuple,)
assert (info.n_fields, info.n_sequence_fields, info.n_unnamed_fields) == (11, 11, 0)
assert not hasattr(info, '__dict__')
assert cls.__flags__ & 256 and not cls.__flags__ & (512 | 1024)
assert repr(info) == 'sys.float_info(max=1.7976931348623157e+308, max_exp=1024, max_10_exp=308, min=2.2250738585072014e-308, min_exp=-1021, min_10_exp=-307, dig=15, mant_dig=53, epsilon=2.220446049250313e-16, radix=2, rounds=1)'
assert str(info) == repr(info)
assert info.min > 0.0 and info.epsilon == 2.0 ** -52
assert info.max * 2.0 == float('inf')
assert cls.__match_args__ == ('max', 'max_exp', 'max_10_exp', 'min', 'min_exp', 'min_10_exp', 'dig', 'mant_dig', 'epsilon', 'radix', 'rounds')
for name, value in zip(cls.__match_args__, expected):
    assert getattr(info, name) == value
    member = cls.__dict__[name]
    assert type(member) is types.MemberDescriptorType
    assert member.__objclass__ is cls and member.__name__ == name
    assert member.__get__(info) == value
    assert isinstance(member.__doc__, str)
    for operation in (lambda: setattr(info, name, 3), lambda: delattr(info, name), lambda: member.__set__(info, 3), lambda: member.__delete__(info)):
        try:
            operation()
        except AttributeError as error:
            assert str(error) == 'readonly attribute'
        else:
            raise AssertionError('mutable float metadata')
assert cls(expected) == info
assert cls(sequence=expected, dict={}) == info
assert cls(range(11), {'unused': 42}) == tuple(range(11))
for size in (10, 12):
    try:
        cls([1] * size)
    except TypeError as error:
        assert str(error) == 'sys.float_info() takes a 11-sequence (' + str(size) + '-sequence given)'
    else:
        raise AssertionError('wrong constructor length accepted')
for args, keywords, message in (
    ((), {}, "structseq() missing required argument 'sequence' (pos 1)"),
    ((1,), {}, 'constructor requires a sequence'),
    ((expected, None), {}, 'sys.float_info() takes a dict as second arg, if any'),
    ((expected,), {'bad': 1}, "'bad' is an invalid keyword argument for structseq()"),
    ((expected,), {'sequence': expected}, "argument for structseq() given by name ('sequence') and position (1)"),
    ((expected, {}, 1), {}, 'structseq() takes at most 2 arguments (3 given)'),
):
    try:
        cls(*args, **keywords)
    except TypeError as error:
        assert str(error) == message
    else:
        raise AssertionError('invalid constructor accepted')
constructor, args = info.__reduce__()
assert constructor is cls and args == (expected, {}) and constructor(*args) == info
assert info.__reduce_ex__(4) == (constructor, args)
try:
    cls.extra = 1
except TypeError as error:
    assert str(error) == "cannot set 'extra' attribute of immutable type 'sys.float_info'"
else:
    raise AssertionError('immutable type accepted attribute')
try:
    class Child(cls):
        pass
except TypeError as error:
    assert str(error) == "type 'sys.float_info' is not an acceptable base type"
else:
    raise AssertionError('struct-sequence accepted subclass')
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
