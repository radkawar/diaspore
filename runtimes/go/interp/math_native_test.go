package interp_test

import (
	"bytes"
	"testing"

	"github.com/radkawar/diaspore/runtimes/go/dspc"
	"github.com/radkawar/diaspore/runtimes/go/interp"
)

func TestNativeMathProtocols(t *testing.T) {
	const source = `import math
assert math.asin(0) == math.atan(0) == 0.0
assert math.asin(1) == math.pi / 2
assert math.atan2(1, 0) == math.pi / 2
assert math.atan2(-0.0, -1.0) == -math.pi
assert math.gamma(6) == 120.0
assert math.isclose(math.gamma(-0.5), -2 * math.sqrt(math.pi))
assert math.expm1(1e-20) == math.log1p(1e-20) == 1e-20
assert math.degrees(math.pi) == 180 and math.radians(180) == math.pi
assert math.trunc(-1.9) == -1 and type(math.trunc(True)) is int
assert math.trunc(1 << 200) == 1 << 200
assert math.remainder(7, 2) == -1 and math.remainder(5, 2) == 1
assert math.copysign(1, math.remainder(-4, 2)) == -1
assert math.remainder(2, math.inf) == 2
assert math.ulp(0) == 5e-324 and math.ulp(1) == 2 ** -52
assert math.ulp(-1) == math.ulp(1)
assert math.ulp(1.7976931348623157e308) == 2 ** 971
assert math.ulp(math.inf) == math.inf
assert math.nextafter(1, 2) == 1 + 2 ** -52
assert math.nextafter(1, 2, steps=3) == 1 + 3 * 2 ** -52
assert math.nextafter(1, 2, steps=1 << 200) == 2
assert math.nextafter(1, math.nan, steps=0) == 1
assert math.nextafter(1, 2, steps=None) == math.nextafter(1, 2)
assert math.copysign(1, math.nextafter(-0.0, 0.0)) == 1
assert math.copysign(1, math.nextafter(0.0, -0.0, steps=0)) == 1
assert math.copysign(1, math.nextafter(-5e-324, 1, steps=1)) == -1
assert math.nextafter(-5e-324, 1, steps=2) == 5e-324
assert math.nextafter(math.inf, 0) == 1.7976931348623157e308
assert math.isclose(a=1, b=1 + 1e-10)
assert not math.isclose(1, 1.1)
assert math.isclose(0, 0.01, abs_tol=0.02)
assert math.isclose(math.inf, math.inf)
assert not math.isclose(math.inf, -math.inf, rel_tol=math.inf)
assert not math.isclose(math.nan, math.nan)
assert math.prod([]) == 1 and math.prod([], start='unchanged') == 'unchanged'
assert math.prod(range(1, 8)) == 5040
assert math.prod([1 << 100, 1 << 100]) == 1 << 200
assert math.prod([2, 3], start=0.5) == 3.0
assert math.prod([2, 3], start='x') == 'xxxxxx'
class Float:
    def __float__(self):
        return 0.5
class Index:
    def __index__(self):
        return 2
assert math.asin(Float()) == math.asin(0.5)
assert math.atan(Index()) == math.atan(2)
assert math.nextafter(1, 2, steps=Index()) == 1 + 2 * 2 ** -52
assert math.isclose(Float(), 0.5, rel_tol=Index())
class FloatDescriptor:
    def __get__(self, obj, cls):
        return lambda: 0.5
class WithDescriptor:
    __float__ = FloatDescriptor()
assert math.atan(WithDescriptor()) == math.atan(0.5)
class Meta(type):
    def __float__(cls):
        return 0.5
class Number(metaclass=Meta):
    pass
assert math.atan(Number) == math.atan(0.5)
class Trunc:
    def __trunc__(self):
        return 'special result'
assert math.trunc(Trunc()) == 'special result'
class BrokenFloat:
    def __float__(self):
        return 1
class BrokenIndex:
    def __index__(self):
        return 1.5
for operation, expected in (
    (lambda: math.asin(2), ValueError),
    (lambda: math.gamma(0), ValueError),
    (lambda: math.gamma(-2), ValueError),
    (lambda: math.gamma(-math.inf), ValueError),
    (lambda: math.gamma(172), OverflowError),
    (lambda: math.expm1(1000), OverflowError),
    (lambda: math.log1p(-1), ValueError),
    (lambda: math.remainder(1, 0), ValueError),
    (lambda: math.remainder(math.inf, 1), ValueError),
    (lambda: math.nextafter(1, 2, steps=-1), ValueError),
    (lambda: math.nextafter(1, 2, 3), TypeError),
    (lambda: math.nextafter(1, 2, steps=1.0), TypeError),
    (lambda: math.isclose(1, 1, abs_tol=-1), ValueError),
    (lambda: math.atan('1'), TypeError),
    (lambda: math.atan(BrokenFloat()), TypeError),
    (lambda: math.atan(BrokenIndex()), TypeError),
    (lambda: math.atan(1 << 10000), OverflowError),
    (lambda: math.trunc(Float()), TypeError),
    (lambda: math.trunc(math.nan), ValueError),
    (lambda: math.trunc(math.inf), OverflowError),
    (lambda: math.atan(x=1), TypeError),
):
    try:
        operation()
    except expected:
        pass
    else:
        raise AssertionError('mathematics error not raised')
for operation in (math.asin, math.atan, math.gamma, math.expm1, math.log1p, math.ulp):
    assert math.isnan(operation(math.nan))
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
